"""
Hermes Reflection Hub: Autonomous reflection service for all 5 trading agents.
Runs every 30 minutes on Railway. No local Mac required.
"""
import asyncio
import json
import yaml
import subprocess
import logging
from datetime import datetime
from pathlib import Path
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGENTS = [
    {
        "name": "MNQ-5M-Candle",
        "repo": "https://github.com/1kenton/MNQ-5M-Candle.git",
        "goal": {"target_return_30d": 0.10, "max_drawdown": 0.08, "min_sharpe": 1.2},
    },
    {
        "name": "BTC-1H-RSI-Coinbase",
        "repo": "https://github.com/1kenton/BTC-1H-RSI-Coinbase.git",
        "goal": {"target_return_30d": 0.20, "max_drawdown": 0.05, "min_sharpe": 1.2},
    },
    {
        "name": "BTC-5M-SupplyDemand",
        "repo": "https://github.com/1kenton/BTC-5M-SupplyDemand.git",
        "goal": {"target_return_30d": 0.20, "max_drawdown": 0.05, "min_sharpe": 1.2},
    },
    {
        "name": "BTC-5M-EMA-Stochastic",
        "repo": "https://github.com/1kenton/BTC-5M-EMA-Stochastic.git",
        "goal": {"target_return_30d": 0.10, "max_drawdown": 0.05, "min_sharpe": 1.2},
    },
    {
        "name": "BTC-5M-BOS-OB",
        "repo": "https://github.com/1kenton/BTC-5M-BOS-OB.git",
        "goal": {"target_return_30d": 0.10, "max_drawdown": 0.05, "min_sharpe": 1.2},
    },
]

def clone_or_pull(repo_url: str, agent_name: str) -> Path:
    """Clone repo if not exists, otherwise pull latest."""
    repo_path = Path(f"/tmp/{agent_name}")
    
    try:
        if repo_path.exists():
            subprocess.run(
                ["git", "-C", str(repo_path), "pull"],
                capture_output=True,
                timeout=30
            )
            logger.info(f"Pulled latest: {agent_name}")
        else:
            subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                capture_output=True,
                timeout=60
            )
            logger.info(f"Cloned repo: {agent_name}")
    except Exception as e:
        logger.error(f"Clone/pull failed for {agent_name}: {e}")
        return None
    
    return repo_path

def load_trades(repo_path: Path) -> list:
    """Load trades from trades.jsonl"""
    trades_file = repo_path / "state" / "trades.jsonl"
    if not trades_file.exists():
        return []
    
    trades = []
    try:
        with open(trades_file) as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))
    except Exception as e:
        logger.error(f"Failed to load trades: {e}")
    
    return trades

def score_trades(trades: list, goal: dict) -> dict:
    """Score trades against goals."""
    if not trades or len(trades) < 5:
        return {"status": "waiting", "trades_count": len(trades), "score": 0.0}
    
    last_5 = trades[-5:]
    returns = [t.get("pnl", 0) for t in last_5]
    total_return = sum(returns)
    win_count = len([r for r in returns if r > 0])
    win_rate = win_count / len(last_5)
    
    target = goal.get("target_return_30d", 0.10)
    failure = goal.get("failure_below", -0.04)
    
    if total_return < failure:
        score = -1.0
    elif total_return >= target:
        score = 1.0
    else:
        score = (total_return - failure) / (target - failure) * 2 - 1
    
    return {
        "status": "ready",
        "trades_analyzed": len(last_5),
        "total_return": total_return,
        "win_rate": win_rate,
        "score": score,
    }

def propose_hypothesis(trades: list, goal: dict, strategy_yaml: dict) -> dict:
    """Propose ONE variable change based on performance."""
    if not trades or len(trades) < 5:
        return None
    
    last_5 = trades[-5:]
    returns = [t.get("pnl", 0) for t in last_5]
    win_count = len([r for r in returns if r > 0])
    win_rate = win_count / len(last_5)
    avg_return = sum(returns) / len(last_5)
    
    hypothesis = None
    
    if win_rate < 0.4:
        hypothesis = {
            "variable": "entry_confirmation_strictness",
            "old_value": "current",
            "new_value": "tighter",
            "reason": f"Win rate {win_rate:.1%} critically low (<40%), tightening entry confirmations",
        }
    elif win_rate < 0.5:
        hypothesis = {
            "variable": "stop_loss_tightness",
            "old_value": "current",
            "new_value": "5% tighter",
            "reason": f"Win rate {win_rate:.1%} below 50%, reducing stop loss distance",
        }
    elif win_rate >= 0.7:
        hypothesis = {
            "variable": "position_size",
            "old_value": "0.5R",
            "new_value": "1.0R",
            "reason": f"Win rate {win_rate:.1%} strong, increasing position size to capture more",
        }
    
    return hypothesis

def reflect_on_agent(agent: dict) -> dict:
    """Full reflection cycle for one agent."""
    logger.info(f"Reflecting on {agent['name']}...")
    
    repo_path = clone_or_pull(agent["repo"], agent["name"])
    if not repo_path:
        return {"agent": agent["name"], "status": "failed"}
    
    trades = load_trades(repo_path)
    score_result = score_trades(trades, agent["goal"])
    
    if score_result["status"] == "waiting":
        logger.info(f"{agent['name']}: waiting for 5 trades ({score_result['trades_count']}/5)")
        return {"agent": agent["name"], "status": "waiting", "trades_count": score_result["trades_count"]}
    
    strategy_file = repo_path / "state" / "strategy.yaml"
    with open(strategy_file) as f:
        strategy = yaml.safe_load(f)
    
    hypothesis = propose_hypothesis(trades, agent["goal"], strategy)
    
    if not hypothesis:
        logger.info(f"{agent['name']}: no hypothesis needed (score: {score_result['score']:.2f})")
        return {"agent": agent["name"], "status": "no_change", "score": score_result["score"]}
    
    # Apply hypothesis
    logger.info(f"{agent['name']}: proposing {hypothesis['variable']} change")
    
    # Save old version to history
    old_version = strategy.get("version", "01")
    new_version = f"{int(old_version) + 1:02d}"
    history_file = repo_path / "state" / "history" / f"v{old_version}.yaml"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "w") as f:
        yaml.dump(strategy, f)
    
    # Update strategy version
    strategy["version"] = new_version
    with open(strategy_file, "w") as f:
        yaml.dump(strategy, f)
    
    # Log hypothesis
    hyp_file = repo_path / "state" / "hypotheses.jsonl"
    hyp_entry = {
        "timestamp": datetime.now().isoformat(),
        "version": new_version,
        "hypothesis": hypothesis,
        "score_before": score_result["score"],
    }
    with open(hyp_file, "a") as f:
        f.write(json.dumps(hyp_entry) + "\n")
    
    # Commit and push
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "add", "-A"],
            capture_output=True,
            timeout=10
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-m", f"v{new_version}: {hypothesis['variable']}"],
            capture_output=True,
            timeout=10
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "push"],
            capture_output=True,
            timeout=30
        )
        logger.info(f"{agent['name']}: pushed v{new_version}")
    except Exception as e:
        logger.error(f"Failed to push {agent['name']}: {e}")
        return {"agent": agent["name"], "status": "failed_to_push"}
    
    return {
        "agent": agent["name"],
        "status": "reflected",
        "version": new_version,
        "hypothesis": hypothesis,
        "score": score_result["score"],
    }

async def reflection_loop():
    """Main loop: reflect every 30 minutes."""
    iteration = 0
    
    while True:
        iteration += 1
        now = datetime.now()
        logger.info(f"\n{'='*60}")
        logger.info(f"[{now.isoformat()}] REFLECTION CYCLE {iteration}")
        logger.info(f"{'='*60}\n")
        
        results = []
        for agent in AGENTS:
            result = reflect_on_agent(agent)
            results.append(result)
        
        logger.info(f"\nCycle {iteration} complete. Results:")
        for r in results:
            logger.info(f"  {r['agent']}: {r['status']}")
        
        logger.info(f"Next reflection in 30 minutes...\n")
        await asyncio.sleep(1800)  # 30 minutes

if __name__ == "__main__":
    logger.info("Booting Hermes Reflection Hub")
    logger.info(f"Monitoring {len(AGENTS)} agents:")
    for agent in AGENTS:
        logger.info(f"  - {agent['name']}")
    logger.info("")
    
    asyncio.run(reflection_loop())
