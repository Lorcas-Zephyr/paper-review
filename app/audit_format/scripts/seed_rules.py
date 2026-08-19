import sys
import os
import asyncio
import yaml
from sqlalchemy import select

# Add parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import db_manager, AgentRule
from core.rule_engine import RuleEngine

async def seed_rules():
    print("Connecting to DB...")
    # Initialize RuleEngine to load rules.yaml
    engine = RuleEngine()
    rules = engine.rules

    print(f"Loaded {len(rules)} rules from rules.yaml")

    try:
        async with db_manager.session() as session:
            for rule_id, rule_content in rules.items():
                # Check if exists
                stmt = select(AgentRule).where(AgentRule.rule_id == rule_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    print(f"Rule {rule_id} already exists. Updating...")
                    existing.content = yaml.safe_dump(rule_content, allow_unicode=True)
                else:
                    print(f"Inserting rule {rule_id}...")
                    new_rule = AgentRule(
                        rule_id=rule_id,
                        content=yaml.safe_dump(rule_content, allow_unicode=True),
                    )
                    session.add(new_rule)

            await session.commit()
            print("Done. Rules seeded successfully.")
    except Exception as e:
        print(f"Failed to seed rules: {e}")
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(seed_rules())
