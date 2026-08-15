# FPL Intelligence System — Autonomous Model Pools
# Follows: spec/AUTONOMOUS_MODEL_POOLS.md

import yaml
from pathlib import Path
from typing import Dict, List, Optional

# Map autonomous role names to pool names
ROLE_TO_POOL = {
    'AUTONOMOUS_PLANNER': 'PLANNING',
    'AUTONOMOUS_IMPLEMENTER': 'IMPLEMENTATION',
    'AUTONOMOUS_REVIEWER': 'REVIEW',
    'AUTONOMOUS_REFEREE': 'REFEREE',
    'AUTONOMOUS_HELPER': 'HELPER',
}


class ModelPoolSelector:
    def __init__(self, config_path: str = "config/ai/autonomous_model_pools.yaml"):
        self.config_path = Path(config_path)
        self.pools = self._load_pools()
        self.approved_providers = self._load_approved_providers()

    def _load_pools(self) -> Dict[str, List[str]]:
        with open(self.config_path, 'r') as file:
            config = yaml.safe_load(file)
        return config.get('pools', {})

    def _load_approved_providers(self) -> List[str]:
        with open(self.config_path, 'r') as file:
            config = yaml.safe_load(file)
        return config.get('approved_providers', [])

    def select_model_for_role(self, role: str, task_type: Optional[str] = None) -> str:
        """Select a model for the given role and task type."""
        pool_name = ROLE_TO_POOL.get(role)
        if pool_name is None:
            raise ValueError(f"Unknown role: {role}")

        if pool_name not in self.pools:
            raise ValueError(f"No pool defined for role {role} (pool {pool_name})")

        models = self.pools[pool_name].get('models', [])
        if not models:
            raise ValueError(f"No models available in pool {pool_name} for role {role}")

        return models[0]

    def is_provider_approved(self, provider: str) -> bool:
        """Check if a provider is in the approved universe."""
        return provider in self.approved_providers

# Singleton instance for application use
model_pool_selector = ModelPoolSelector()

# Convenience function for direct use
select_model_for_role = model_pool_selector.select_model_for_role
is_provider_approved = model_pool_selector.is_provider_approved