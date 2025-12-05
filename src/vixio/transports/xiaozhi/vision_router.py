"""
Vision Router - Vision analysis endpoints for Xiaozhi devices

Provides vision analysis endpoints:
- GET /mcp/vision/explain - Health check
- POST /mcp/vision/explain - Image analysis with VLM
- OPTIONS /mcp/vision/explain - CORS preflight

Authentication:
- Uses self-contained JWT token (contains session_id)
- Token is generated during hello handshake
- Session can be traced back to device_id via transport mapping

Note:
- Uses manual multipart parsing to support chunked transfer encoding
- Device sends multipart/form-data with Transfer-Encoding: chunked

Test:
curl -X POST "http://localhost:8000/mcp/vision/explain" \
  -H "Device-Id: test-device" \
  -H "Authorization: Bearer test-token" \
  -F "question=What is in the image?" \
  -F "file=@tests/test_images/1.jpeg" \
  2>&1

"""

from typing import Optional, Dict, Tuple

from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse
from loguru import logger

from vixio.transports.xiaozhi.auth import XiaozhiAuth


# Maximum file size: 5MB
MAX_FILE_SIZE = 5 * 1024 * 1024

# Supported image formats (magic bytes)
IMAGE_SIGNATURES = {
    b'\xff\xd8\xff': 'image/jpeg',           # JPEG
    b'\x89PNG\r\n\x1a\n': 'image/png',       # PNG
    b'GIF87a': 'image/gif',                  # GIF87a
    b'GIF89a': 'image/gif',                  # GIF89a
    b'BM': 'image/bmp',                      # BMP
    b'II*\x00': 'image/tiff',                # TIFF (little endian)
    b'MM\x00*': 'image/tiff',                # TIFF (big endian)
    b'RIFF': 'image/webp',                   # WebP (need further check)
}


def is_valid_image(data: bytes) -> Tuple[bool, str]:
    """
    Check if data is a valid image file.
    
    Args:
        data: Raw image bytes
        
    Returns:
        Tuple of (is_valid, mime_type)
    """
    for signature, mime_type in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            # Special check for WebP (RIFF....WEBP)
            if signature == b'RIFF' and len(data) >= 12:
                if data[8:12] != b'WEBP':
                    continue
            return True, mime_type
    return False, ""


def create_vision_router(
    config: Dict,
    auth: XiaozhiAuth,
) -> APIRouter:
    """
    Create Vision router with dependencies.
    
    Args:
        config: Server configuration dict
        auth: XiaozhiAuth instance for token verification
        
    Returns:
        Configured APIRouter instance
    """
    router = APIRouter(prefix="/mcp/vision", tags=["vision"])
    log = logger.bind(component="VisionRouter")
    
    def _verify_auth_token(authorization: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify Bearer token from Authorization header.
        
        Token is self-contained JWT with session_id.
        
        Args:
            authorization: Authorization header value
            
        Returns:
            Tuple of (is_valid, error_message, session_id)
        """
        if not authorization.startswith("Bearer "):
            return False, "Invalid authorization format", None
        
        token = authorization[7:]  # Remove "Bearer " prefix
        
        # Verify self-contained vision token
        # Returns (is_valid, session_id_or_device_id)
        try:
            is_valid, identifier = auth.verify_vision_token(token)
            if is_valid:
                return True, None, identifier
            return False, "Invalid or expired token", None
        except Exception as e:
            log.error(f"Token verification error: {e}")
            return False, "Token verification failed", None
    
    def _create_error_response(message: str, status_code: int = 400) -> JSONResponse:
        """Create unified error response with automatic logging."""
        # Auto-log all error responses
        log.error(f"[Vision] Error response: status={status_code}, message={message}")
        return JSONResponse(
            content={"success": False, "message": message},
            status_code=status_code,
            headers={
                "Access-Control-Allow-Headers": "client-id, content-type, device-id, authorization",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Origin": "*",
            }
        )
    
    def _add_cors_headers(response: JSONResponse) -> JSONResponse:
        """Add CORS headers to response."""
        response.headers["Access-Control-Allow-Headers"] = "client-id, content-type, device-id, authorization"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    
    @router.get("/explain")
    async def vision_get():
        """
        Handle Vision GET request - health check.
        
        Returns:
            Text message about vision interface status
        """
        try:
            server_config = config.get("server", {})
            vision_explain = server_config.get("vision_explain", "")
            
            if vision_explain:
                message = f"MCP Vision interface is running, vision explain URL: {vision_explain}"
            else:
                message = "MCP Vision interface is running. Configure server.vision_explain in config to set external URL."
            
            response = JSONResponse({"message": message, "status": "available"})
            return _add_cors_headers(response)
            
        except Exception as e:
            log.error(f"Vision GET request error: {e}")
            return _create_error_response("Server internal error", 500)
    
    @router.post("/explain")
    async def vision_post(request: Request):
        """
        Handle Vision POST request - image analysis.
        
        Supports chunked transfer encoding by manually parsing multipart form data.
        
        Expected multipart fields (in order):
        1. question: Text field with the question
        2. file: Image file (JPEG, PNG, etc.)
        
        Headers:
        - Authorization: Bearer token
        - Device-Id: Device ID
        - Client-Id: Client ID (optional)
            
        Returns:
            JSON with analysis result
        """
        session_id = None
        try:
            # Get headers
            authorization = request.headers.get("Authorization", "")
            device_id = request.headers.get("Device-Id", "")
            client_id = request.headers.get("Client-Id", "")
            
            if not authorization:
                return _create_error_response("Missing Authorization header", 401)
            if not device_id:
                return _create_error_response("Missing Device-Id header", 400)
            
            log.info(f"[Vision] Processing request from device_id={device_id}")
            
            # Verify authentication
            server_config = config.get("server", {})
            auth_config = server_config.get("auth", {})
            
            if auth_config.get("enabled", False):
                is_valid, error_msg, session_id = _verify_auth_token(authorization)
                if not is_valid:
                    return _create_error_response(error_msg or "Authentication failed", 401)
            
            # Parse multipart form data (supports chunked encoding)
            log.info("[Vision] Parsing multipart form data...")
            try:
                form = await request.form()
                log.info(f"[Vision] Form parsed, keys: {list(form.keys())}")
            except Exception as e:
                log.error(f"[Vision] Failed to parse form: {type(e).__name__}: {e}")
                import traceback
                log.error(f"[Vision] Traceback: {traceback.format_exc()}")
                return _create_error_response(f"Failed to parse form data: {str(e)}", 400)
            
            # Get question field
            question = form.get("question")
            if not question:
                log.error("[Vision] Missing 'question' field in form")
                return _create_error_response("Missing 'question' field")
            question = str(question)
            log.info(f"[Vision] Question: {question[:50]}...")
            
            # Get file field
            file = form.get("file")
            if not file:
                log.error("[Vision] Missing 'file' field in form")
                return _create_error_response("Missing 'file' field")
            
            log.info(f"[Vision] File received, type: {type(file)}")
            
            # Read image data
            image_data = await file.read()
            
            if not image_data:
                return _create_error_response("Image data is empty")
            
            # Check file size
            if len(image_data) > MAX_FILE_SIZE:
                return _create_error_response(
                    f"Image size exceeds limit, maximum allowed: {MAX_FILE_SIZE / 1024 / 1024}MB"
                )
            
            # Validate image format
            is_valid, mime_type = is_valid_image(image_data)
            if not is_valid:
                return _create_error_response(
                    "Unsupported file format. Supported formats: JPEG, PNG, GIF, BMP, TIFF, WEBP"
                )
            
            # Get VLM provider configuration
            # Support both flat and nested config structures:
            # Flat: providers.vlm
            # Nested: dev.providers.vlm (or docker.providers.vlm, k8s.providers.vlm)
            providers_config = config.get("providers", {})
            vlm_provider_config = providers_config.get("vlm", {})
            
            # If not found at top level, try environment-specific paths
            if not vlm_provider_config:
                for env in ["dev", "docker", "k8s"]:
                    env_config = config.get(env, {})
                    env_providers = env_config.get("providers", {})
                    vlm_provider_config = env_providers.get("vlm", {})
                    if vlm_provider_config:
                        log.info(f"[Vision] Found VLM config under '{env}.providers.vlm'")
                        break
            
            if not vlm_provider_config:
                return _create_error_response("VLM provider not configured (checked: providers.vlm, dev/docker/k8s.providers.vlm)")
            
            provider_name = vlm_provider_config.get("provider")
            provider_params = vlm_provider_config.get("config", {})
            
            if not provider_name:
                return _create_error_response("VLM provider name not specified")
            
            # Create VLM instance using ProviderFactory
            try:
                from vixio.providers.factory import ProviderFactory, _expand_env_vars
                from vixio.providers.vision import ImageContent
                # Ensure provider is registered
                import providers.vision_describers  # noqa: F401
                
                # Expand environment variables in config (e.g., ${GLM_API_KEY})
                expanded_params = _expand_env_vars(provider_params)
                describer = ProviderFactory.create(provider_name, expanded_params)
                
                # Create ImageContent with raw bytes
                # Provider handles format conversion internally
                image_content = ImageContent(
                    image=image_data,  # Raw bytes, not base64
                    mime_type=mime_type,
                    source="upload",
                    trigger="request",
                )
                
                # Get description
                result = await describer.describe(image_content, question)
                
                response = JSONResponse({
                    "success": True,
                    "action": "RESPONSE",
                    "response": result,
                })
                return _add_cors_headers(response)
                
            except ValueError as e:
                log.error(f"VLM provider not found: {e}")
                return _create_error_response(f"VLM provider error: {str(e)}", 500)
            except ImportError as e:
                log.error(f"Failed to import VLM modules: {e}")
                return _create_error_response("VLM module not available", 500)
            except Exception as e:
                log.error(f"VLM processing error: {e}")
                return _create_error_response(f"Vision analysis failed: {str(e)}", 500)
                
        except ValueError as e:
            log.error(f"Vision POST request validation error: {e}")
            return _create_error_response(str(e))
        except Exception as e:
            log.error(f"Vision POST request error: {e}", exc_info=True)
            return _create_error_response("Processing request failed", 500)
    
    @router.options("/explain")
    async def vision_options():
        """
        Handle Vision OPTIONS request - CORS preflight.
        
        Returns:
            Empty JSON with CORS headers
        """
        return JSONResponse(
            content={},
            headers={
                "Access-Control-Allow-Headers": "client-id, content-type, device-id, authorization",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            }
        )
    
    return router

