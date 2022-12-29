import os
import shutil
from typing import Any, AnyStr, Dict

from fastapi import FastAPI, UploadFile
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

from settings import settings

app = FastAPI()

# FIXME tighten this up

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JSONObject = Dict[AnyStr, Any]

logger.info("** Started server **")

os.makedirs(settings.CACHE_DIR, exist_ok=True)


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.post('/upload')
def upload(file: UploadFile):
    path = os.path.join(settings.CACHE_DIR, file.filename)
    if os.path.exists(path):
        return {"message": "Exists"}
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}

