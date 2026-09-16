"""Run independently: uvicorn backend.app.kb.management_app:create_app --factory --port 8011."""
import os
from pathlib import Path
from .rpc import app_for,service_key

def create_app():
    from .management import Management
    root=Path(os.environ.get('KB_DATA_DIR',str(Path(os.environ.get('DATA_DIR','.data'))/'knowledge')))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    domain=Management(os.environ.get('KB_MANAGEMENT_DATABASE_URL',f'sqlite:///{root}/management.db'),root/'documents',service_key())
    return app_for(domain,'management',{'create','list','get','update','rebuild','upload','retry','remove_document','delete','cancel','resolve','verify','claim','artifact','download','progress','renew','fail','publish','cleanup_list','cleanup_result','build_status'})
