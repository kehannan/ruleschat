import asyncio
import websockets

async def test_websocket():
    uri = "ws://127.0.0.1:8000/ws/chat/"  # Ensure this matches your FastAPI WebSocket route
    async with websockets.connect(uri) as websocket:
        print("✅ Connected to WebSocket!")
        
        await websocket.send("Hello, WebSocket!")
        response = await websocket.recv()
        print("🔄 Response from server:", response)

asyncio.run(test_websocket())
