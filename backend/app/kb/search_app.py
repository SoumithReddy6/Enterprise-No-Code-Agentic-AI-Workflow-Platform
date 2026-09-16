"""Run independently: uvicorn backend.app.kb.search_app:create_app --factory --port 8012."""
import os
from pathlib import Path
from .rpc import app_for,service_key

def create_app():
    from .search import Search
    root=Path(os.environ.get('KB_DATA_DIR',str(Path(os.environ.get('DATA_DIR','.data'))/'knowledge')))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    domain=Search(os.environ.get('KB_SEARCH_DATABASE_URL',f'sqlite:///{root}/search.db'),root/'indexes',service_key())
    return app_for(domain,'retrieval-index',{'build','search','verify','cleanup','cleanup_pending','stats','preview'},async_domain=True)
