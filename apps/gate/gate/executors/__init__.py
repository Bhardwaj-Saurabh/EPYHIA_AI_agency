from ..pipeline import Executor
from .deploy import deploy_executor
from .storage import (
    business_storage_executor,
    run_shell_executor,
    site_storage_executor,
    task_storage_executor,
)

# Executor registry. Action types without an executor are capability-checked
# and audited but return 501 until their build-order step lands.
EXECUTORS: dict[str, Executor] = {
    "deploy": deploy_executor,
    "run_shell": run_shell_executor,
    "business_storage": business_storage_executor,
    "task_storage": task_storage_executor,
    "site_storage": site_storage_executor,
}
