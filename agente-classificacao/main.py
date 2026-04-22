import uvicorn
from src.api import app

if __name__ == "__main__":
    print("Routes registered:")
    for route in app.routes:
        print(f"  {route.path}")
    uvicorn.run("src.api:app", host="0.0.0.0", port=8001, reload=True, log_level="info")

