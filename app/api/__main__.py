import os

import uvicorn

uvicorn.run("app.api.main:app", host=os.environ.get("APP_HOST", "127.0.0.1"), port=int(os.environ.get("APP_PORT", "8000")))
