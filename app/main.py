# app/main.py

from fastapi import FastAPI
from app.routes import router

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="API Users")



app.include_router(router, prefix="/api")