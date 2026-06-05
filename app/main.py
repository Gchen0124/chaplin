import os

from app.api import create_app
from app.keys import KeyStore
from app.settings import get_settings
from app.store import Store
from app.vsr import VSR


def build():
    settings = get_settings()
    store = Store(settings.db_path)
    store.init_db()
    keyfile = os.path.join(os.path.expanduser("~"), ".chaplin", "keys.json")
    keys = KeyStore(keyfile)
    print("Loading Chaplin VSR model...")
    vsr = VSR(settings.vsr_config, device="cpu")
    print("Chaplin VSR model loaded.")
    return create_app(settings=settings, store=store, keys=keys, vsr=vsr)


app = build()
