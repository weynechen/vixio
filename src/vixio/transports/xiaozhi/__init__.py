"""
Xiaozhi transport implementation

Provides WebSocket and HTTP endpoints for Xiaozhi devices using FastAPI
"""

from vixio.transports.xiaozhi.transport import XiaozhiTransport
from vixio.transports.xiaozhi.protocol import (
    XiaozhiProtocol,
    XiaozhiMessageType,
    XiaozhiControlAction,
)
from vixio.transports.xiaozhi.device_tools import XiaozhiDeviceToolClient
from vixio.transports.xiaozhi.auth import XiaozhiAuth, generate_mqtt_password
from vixio.transports.xiaozhi.ota_router import create_ota_router
from vixio.transports.xiaozhi.vision_router import create_vision_router

__all__ = [
    "XiaozhiTransport",
    "XiaozhiProtocol",
    "XiaozhiMessageType",
    "XiaozhiControlAction",
    "XiaozhiDeviceToolClient",
    "XiaozhiAuth",
    "generate_mqtt_password",
    "create_ota_router",
    "create_vision_router",
]

