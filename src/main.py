import asyncio
import os
import shutil

from fastapi import FastAPI, UploadFile
from fastapi_exceptions.exceptions import ValidationError
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import ws
from common import k8common
from common.utils import disable_hc_logging
from logs import configure_logging
from settings import settings

app = FastAPI()

# FIXME tighten CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


disable_hc_logging()
logger.info("** Started server **")


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.on_event("shutdown")
async def shutdown_event():
    ws.shutting_down = True
    if ws.mainsocket:
        await ws.mainsocket.close()


def store_file(path: str, file: UploadFile):
    if os.path.exists(path):
        raise ValidationError({"message": "Exists"})
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()


@app.post('/upload')
def upload_cache(file: UploadFile):
    logger.info(f"Uploading file {file.filename} to cache")
    os.makedirs(settings.CACHE_DIR, exist_ok=True)
    path = os.path.join(settings.CACHE_DIR, file.filename)
    if os.path.exists(path):
        return {"message": "Exists"}
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}


async def create_tasks():
    config = Config(app, port=5000, host='0.0.0.0')
    config.setup_event_loop()
    server = Server(config=config)
    t1 = asyncio.create_task(ws.connect_websocket())
    t2 = asyncio.create_task(server.serve())
    await asyncio.gather(t1, t2)

# Unless I want to add external retry support I don't need to know when a spec is finished:
# I can assume that each spec is owned by a single runner

# @app.post('/testrun/{trid}/completed-spec/{specid}')
# async def completed_spec(trid: int, specid: int):
#     mark_spec_completed(trid, specid)

if __name__ == "__main__":
    try:
        if settings.JOB_MODE == 'k8':
            k8common.init()
        configure_logging()
        asyncio.run(create_tasks())
    except Exception as ex:
        print(ex)

