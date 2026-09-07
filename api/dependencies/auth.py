"""The legacy import path now enforces auth v2 bearer sessions everywhere."""
from api.auth.dependencies import get_authenticated_user_id as get_user_id
