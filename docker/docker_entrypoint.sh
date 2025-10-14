#!/bin/sh
#Using this script to start the API allows environmental variables to take effect

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"