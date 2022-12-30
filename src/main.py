import os
import shutil

import httpx as httpx
from fastapi import FastAPI, UploadFile, Response
from fastapi_exceptions.exceptions import ValidationError, NotFound
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import testruns
from common.enums import TestRunStatus
from common.schemas import TestRunSpecs, TestRunDetail, TestRunSpec
from common.utils import get_headers
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

logger.info("** Started server **")


@app.on_event("startup")
def init_folders():
    os.makedirs(settings.CACHE_DIR, exist_ok=True)


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


def store_file(path: str, file: UploadFile):
    if os.path.exists(path):
        raise ValidationError({"message": "Exists"})
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()


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


@app.get('/testrun/{trid}', response_model=TestRunDetail)
def get_testrun(trid: int) -> TestRunDetail:
    tr = testruns.get_run(trid)
    if not tr:
        raise NotFound()
    return tr


@app.put('/testrun/{trid}/status/{status}')
def update_status(trid: int, status: TestRunStatus):
    testruns.update_status(trid, status)
    # and tell cykube
    httpx.put(f'{settings.MAIN_API_URL}/agent/testrun/{trid}/status/{status}',
              headers=get_headers())


@app.put('/testrun/{trid}/specs', response_model=TestRunDetail)
def hub_set_specs(trid: int, item: TestRunSpecs) -> TestRunDetail:
    # tell cykube main
    r = httpx.put(f'{settings.MAIN_API_URL}/agent/testrun/{trid}/specs',
                  headers=get_headers(),
                  json={'specs': item.specs, 'sha': item.sha})
    if r.status_code != 200:
        raise ValidationError(f"Failed to update test run: {r.text}")
    # we'll have a full test run now
    testrun = TestRunDetail.parse_obj(r.json())
    testruns.update_run(testrun)
    return testrun


@app.get('/testrun/{pk}/next-spec', response_model=TestRunSpec | None)
async def get_next_testrun_spec(pk: int, response: Response):
    spec = testruns.get_next_spec(pk)
    if not spec:
        # no spec - we're done
        response.status_code = 204
        return
    # tell cykube
    httpx.post(f'{settings.MAIN_API_URL}/agent/testrun/{pk}/spec-started/{spec.id}',
               headers=get_headers())
    return spec


# Unless I want to add external retry support I don't need to know when a spec is finished:
# I can assume that each spec is owned by a single runner

# @app.post('/testrun/{trid}/completed-spec/{specid}')
# async def completed_spec(trid: int, specid: int):
#     mark_spec_completed(trid, specid)

