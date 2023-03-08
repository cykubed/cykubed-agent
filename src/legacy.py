
app = FastAPI()

disable_hc_logging()

logger.info("** Started server **")


@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.on_event("shutdown")
async def shutdown_event():
    logger.error("Received shutdown event")
    shutdown()
    if ws.mainsocket:
        await ws.mainsocket.close()


@app.post('/upload')
def upload_cache(file: UploadFile):
    logger.info(f"Uploading file {file.filename} to cache")
    path = os.path.join(settings.CYKUBE_CACHE_DIR, file.filename)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}


@app.post('/log')
def post_log(msg: AgentLogMessage):
    """
    Proxy all log messages up to the main server
    :param msg:
    :return:
    """
    messages.queue.add_agent_msg(msg)


@app.get('/testrun/{pk}', response_model=NewTestRun)
async def get_test_run(pk: int) -> NewTestRun:
    tr = await mongo.get_testrun(pk)
    if not tr:
        raise NotFound()
    return NewTestRun.parse_obj(tr)


@app.get('/testrun/{pk}/next', response_class=PlainTextResponse)
async def get_next_spec(pk: int, response: Response, name: str = None) -> str:
    tr = await mongo.get_testrun(pk)
    if not tr:
        raise NotFound()

    if tr['status'] != 'running':
        response.status_code = 204
        return
    spec = await mongo.assign_next_spec(pk, name)
    if not spec:
        response.status_code = 204
        return

    messages.queue.add_agent_msg(AgentSpecStarted(testrun_id=pk,
                                                  type=AgentEventType.spec_started,
                                                  started=utcnow(),
                                                  pod_name=name,
                                                  file=spec))
    return spec


@app.post('/testrun/{pk}/status/{status}')
async def status_changed(pk: int, status: TestRunStatus):
    await mongo.set_status(pk, status)
    messages.queue.add_agent_msg(AgentStatusChanged(testrun_id=pk,
                                                    type=AgentEventType.status,
                                                    status=status))


@app.post('/testrun/{pk}/spec-terminated')
async def spec_terminated(pk: int, item: SpecTerminated):
    # for now we assume file uploads go straight to cykubemain
    await mongo.spec_terminated(pk, item.file)
    messages.queue.add_agent_msg(AgentSpecTerminated(type=AgentEventType.spec_terminated, testrun_id=pk,
                                                     file=item.file))


@app.post('/testrun/{pk}/spec-completed')
async def spec_completed(pk: int, item: CompletedSpecFile):
    # for now we assume file uploads go straight to cykubemain
    await mongo.spec_completed(pk, item)
    messages.queue.add_agent_msg(AgentSpecCompleted(type=AgentEventType.spec_completed, testrun_id=pk, spec=item))


@app.post('/testrun/{pk}/build-complete')
async def build_complete(pk: int, build: CompletedBuild):
    tr = await mongo.set_build_details(pk, build)
    messages.queue.add_agent_msg(AgentCompletedBuildMessage(testrun_id=pk,
                                                            type=AgentEventType.build_completed,
                                                            build=build))

    if settings.K8:
        if not tr.project.start_runners_first:
            create_runner_jobs(tr, build)
    else:
        logger.info(f'Start runner with "./main.py run {pk}', id=pk)

    return {"message": "OK"}


@app.on_event("startup")
@repeat_every(seconds=300)
async def cleanup_cache():
    await cleanup()


# @app.on_event("startup")
# @repeat_every(seconds=120)
# async def cleanup_testruns():
#     # check for specs that are marked as running but have no pod
#     if settings.K8:
#         loop = asyncio.get_running_loop()
#         for doc in await mongo.get_active_specfile_docs():
#             podname = doc['pod_name']
#             is_running = await loop.run_in_executor(None, is_pod_running, podname)
#             if not is_running:
#                 tr = await mongo.get_testrun(doc['trid'])
#                 if tr and tr['status'] == 'running':
#                     file = doc['file']
#                     # let another Job take this - this handles crashes and Spot Jobs
#                     logger.info(f'Cannot find a running pod for {podname}: returning {file} to the pool')
#                     await mongo.reset_specfile(doc)

    # # now check for builds that have gone on for too long
    # for tr in await mongo.get_testruns_with_status(TestRunStatus.building):
    #     duration = (datetime.datetime.utcnow() - tr['started']).seconds
    #     if duration > tr['project']['build_deadline']:
    #         await mongo.cancel_testrun(tr['id'])
    #
    # # ditto for runners
    # try:
    #     for tr in await mongo.get_testruns_with_status(TestRunStatus.running):
    #         duration = (datetime.datetime.utcnow() - tr['started']).seconds
    #         if duration > tr['project']['runner_deadline']:
    #             await mongo.cancel_testrun(tr['id'])
    # except:
    #     logger.exception("Failed to cleanup testruns")
