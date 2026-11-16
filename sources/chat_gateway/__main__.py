import uvicorn

uvicorn.run("sources.chat_gateway.app:app", host="127.0.0.1", port=8001)
