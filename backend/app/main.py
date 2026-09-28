import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.workspaces import DEMO_ID, WorkspaceManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    workspaces = WorkspaceManager(get_settings())
    demo = await asyncio.to_thread(workspaces.get, DEMO_ID)  # warm the demo workspace
    try:
        # The Hindsight SDK's sync wrappers run their own event loop, so keep them off FastAPI's loop.
        await asyncio.to_thread(demo.memory.setup_bank, False)
    except Exception as e:  # keep serving; memory errors surface per request
        logging.getLogger(__name__).error("Hindsight bank setup failed: %s", e)
    app.state.workspaces = workspaces
    yield


app = FastAPI(title="SpendSight", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
