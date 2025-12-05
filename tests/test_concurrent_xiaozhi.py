"""
Concurrent integration test for Xiaozhi server.

Tests the complete flow from connection, audio/text sending, to receiving and playback
with configurable concurrency, random interruptions, and disconnections.
"""

import asyncio
from ipaddress import ip_address
import json
import random
import sys
import time
from pathlib import Path
from typing import Optional
import wave

import websockets
from loguru import logger

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vixio.transports.xiaozhi.protocol import (
    XiaozhiMessageType,
    XiaozhiControlAction,
)
from vixio.utils.audio import OpusCodec, get_opus_codec


class AudioPlayer:
    """Simulated audio player that logs instead of playing."""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.total_audio_bytes = 0
        self.audio_chunks = 0
    
    def play(self, audio_data: bytes):
        """Simulate playing audio (just log)."""
        self.total_audio_bytes += len(audio_data)
        self.audio_chunks += 1
        logger.debug(
            f"[Client-{self.client_id}] Playing audio chunk #{self.audio_chunks}: "
            f"{len(audio_data)} bytes (total: {self.total_audio_bytes} bytes)"
        )
    
    def get_stats(self) -> dict:
        """Get playback statistics."""
        return {
            "total_audio_bytes": self.total_audio_bytes,
            "audio_chunks": self.audio_chunks,
        }


class ConcurrentTestClient:
    """Test client with full flow simulation."""
    
    def __init__(
        self,
        client_id: str,
        server_url: str,
        test_audio_opus: list[bytes],
        interrupt_probability: float = 0.1,
        disconnect_probability: float = 0.05,
    ):
        """
        Initialize test client.
        
        Args:
            client_id: Unique client identifier
            server_url: WebSocket server URL
            test_audio_opus: Pre-encoded Opus audio frames
            interrupt_probability: Probability of interrupting during audio send
            disconnect_probability: Probability of random disconnect
        """
        self.client_id = client_id
        self.server_url = server_url
        self.session_id = f"test-client-{client_id}"
        self.test_audio_opus = test_audio_opus
        self.interrupt_probability = interrupt_probability
        self.disconnect_probability = disconnect_probability
        
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.player = AudioPlayer(client_id)
        
        # Statistics
        self.stats = {
            "connections": 0,
            "disconnections": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "audio_frames_sent": 0,
            "audio_bytes_received": 0,
            "text_messages_sent": 0,
            "text_messages_received": 0,
            "interruptions": 0,
            "random_disconnects": 0,
            "errors": 0,
        }
    
    async def connect(self) -> bool:
        """Connect to server."""
        try:
            logger.info(f"[Client-{self.client_id}] Connecting to {self.server_url}")
            self.websocket = await websockets.connect(self.server_url)
            self.connected = True
            self.stats["connections"] += 1
            logger.success(f"[Client-{self.client_id}] Connected successfully")
            return True
        except Exception as e:
            logger.error(f"[Client-{self.client_id}] Connection failed: {e}")
            self.stats["errors"] += 1
            self.connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from server."""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
            self.stats["disconnections"] += 1
            logger.info(f"[Client-{self.client_id}] Disconnected")
    
    async def send_hello(self):
        """Send hello message."""
        if not self.connected or not self.websocket:
            return
        
        message = {
            "type": XiaozhiMessageType.HELLO,
            "version": 1,
            "session_id": self.session_id,
            "device_type": "ESP32-Test",
            "firmware_version": "1.0.0-test",
        }
        
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
        self.stats["messages_sent"] += 1
        logger.debug(f"[Client-{self.client_id}] Sent hello message")
    
    async def send_audio_stream(self, interrupt_after: Optional[int] = None) -> bool:
        """
        Send audio stream to server.
        
        Args:
            interrupt_after: Interrupt after N frames (None = no interrupt)
            
        Returns:
            True if completed without interrupt, False if interrupted
        """
        if not self.connected or not self.websocket:
            return False
        
        logger.info(
            f"[Client-{self.client_id}] Sending audio stream "
            f"({len(self.test_audio_opus)} frames)"
        )
        
        for i, opus_frame in enumerate(self.test_audio_opus):
            # Check for interrupt
            if interrupt_after is not None and i >= interrupt_after:
                logger.warning(
                    f"[Client-{self.client_id}] Interrupting audio stream at frame {i}"
                )
                await self.send_interrupt()
                return False
            
            # Send audio frame
            await self.websocket.send(opus_frame)
            self.stats["audio_frames_sent"] += 1
            
            # Small delay to simulate real-time streaming (60ms per frame)
            await asyncio.sleep(0.06)
        
        self.stats["messages_sent"] += 1
        logger.success(
            f"[Client-{self.client_id}] Audio stream completed "
            f"({self.stats['audio_frames_sent']} frames)"
        )
        return True
    
    async def send_interrupt(self):
        """Send interrupt control message."""
        if not self.connected or not self.websocket:
            return
        
        message = {
            "type": XiaozhiMessageType.CONTROL,
            "action": XiaozhiControlAction.INTERRUPT,
            "session_id": self.session_id,
        }
        
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
        self.stats["messages_sent"] += 1
        self.stats["interruptions"] += 1
        logger.warning(f"[Client-{self.client_id}] Sent interrupt")
    
    async def send_text(self, text: str):
        """Send text message."""
        if not self.connected or not self.websocket:
            return
        
        message = {
            "type": XiaozhiMessageType.TEXT,
            "content": text,
            "session_id": self.session_id,
        }
        
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
        self.stats["messages_sent"] += 1
        self.stats["text_messages_sent"] += 1
        logger.info(f"[Client-{self.client_id}] Sent text: {text}")
    
    async def receive_and_process(self) -> Optional[dict]:
        """Receive and process one message from server."""
        if not self.connected or not self.websocket:
            return None
        
        try:
            data = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            
            # Parse message
            if isinstance(data, str):
                message = json.loads(data)
                msg_type = message.get('type', 'unknown')
                self.stats["messages_received"] += 1
                
                if msg_type == XiaozhiMessageType.TEXT:
                    self.stats["text_messages_received"] += 1
                    content = message.get('content', '')
                    logger.info(
                        f"[Client-{self.client_id}] Received text: {content[:50]}..."
                    )
                elif msg_type == XiaozhiMessageType.HELLO:
                    logger.debug(f"[Client-{self.client_id}] Received hello response")
                else:
                    logger.debug(f"[Client-{self.client_id}] Received {msg_type}")
                
                return message
                
            elif isinstance(data, bytes):
                # Audio data - simulate playback
                self.stats["messages_received"] += 1
                self.stats["audio_bytes_received"] += len(data)
                self.player.play(data)
                
                return {"type": "audio", "size": len(data)}
            
        except asyncio.TimeoutError:
            logger.debug(f"[Client-{self.client_id}] Receive timeout (no more data)")
            return None
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[Client-{self.client_id}] Connection closed by server")
            self.connected = False
            return None
        except Exception as e:
            logger.error(f"[Client-{self.client_id}] Receive error: {e}")
            self.stats["errors"] += 1
            return None
    
    async def receive_loop(self, duration: float = 30.0):
        """
        Continuously receive messages for a duration.
        
        Args:
            duration: Maximum duration to receive (seconds)
        """
        logger.info(f"[Client-{self.client_id}] Starting receive loop for {duration}s")
        start_time = time.time()
        
        while self.connected and (time.time() - start_time) < duration:
            message = await self.receive_and_process()
            if message is None:
                break
            
            # Check for random disconnect
            if random.random() < self.disconnect_probability:
                logger.warning(
                    f"[Client-{self.client_id}] Random disconnect triggered"
                )
                self.stats["random_disconnects"] += 1
                await self.disconnect()
                break
        
        logger.info(f"[Client-{self.client_id}] Receive loop ended")
    
    async def run_full_test_cycle(self):
        """Run a complete test cycle."""
        logger.info(f"[Client-{self.client_id}] Starting full test cycle")
        
        try:
            # Connect
            if not await self.connect():
                return
                        
            # Send hello
            await self.send_hello()
            await asyncio.sleep(0.3)

            # recieve hello
            await self.receive_and_process()
            
            # Test 1: Send audio with possible interrupt
            should_interrupt = random.random() < self.interrupt_probability
            if should_interrupt:
                interrupt_at = random.randint(5, min(15, len(self.test_audio_opus) - 1))
                interrupted = not await self.send_audio_stream(interrupt_after=interrupt_at)
                
                if interrupted:
                    # After interrupt, wait a bit then send audio again
                    await asyncio.sleep(1.0)
                    logger.info(
                        f"[Client-{self.client_id}] Resending audio after interrupt"
                    )
                    await self.send_audio_stream()
            else:
                await self.send_audio_stream()
            
            # Receive responses (audio and text)
            receive_task = asyncio.create_task(self.receive_loop(duration=10.0))
            await receive_task
            
            # Test 2: Send text message
            await self.send_text(f"Test message from client {self.client_id}")
            
            # Receive text response
            receive_task = asyncio.create_task(self.receive_loop(duration=5.0))
            await receive_task
            
            # Disconnect
            await self.disconnect()
            
            logger.success(f"[Client-{self.client_id}] Test cycle completed")
            
        except Exception as e:
            logger.error(f"[Client-{self.client_id}] Test cycle error: {e}")
            self.stats["errors"] += 1
            import traceback
            traceback.print_exc()
        finally:
            if self.connected:
                await self.disconnect()
    
    def get_stats(self) -> dict:
        """Get client statistics."""
        return {
            **self.stats,
            "player": self.player.get_stats(),
        }


async def load_test_audio(audio_path: Path) -> list[bytes]:
    """
    Load test audio file and convert to Opus frames.
    
    Args:
        audio_path: Path to WAV file
        
    Returns:
        List of Opus encoded frames
    """
    logger.debug(f"Loading test audio: {audio_path.name}")
    
    try:
        # Read WAV file
        with wave.open(str(audio_path), 'rb') as wf:
            # Get audio parameters
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            logger.debug(
                f"  {audio_path.name}: {channels}ch, {sample_width*8}bit, "
                f"{framerate}Hz, {n_frames} frames"
            )
            
            # Read all frames
            pcm_data = wf.readframes(n_frames)
        
        # Convert to 16kHz mono if needed
        if framerate != 16000 or channels != 1 or sample_width != 2:
            logger.debug(f"  Converting {audio_path.name} to 16kHz mono 16-bit...")
            from pydub import AudioSegment
            from io import BytesIO
            
            # Load audio
            audio = AudioSegment.from_wav(str(audio_path))
            
            # Convert
            audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            
            # Get PCM data
            pcm_data = audio.raw_data
        
        # Encode to Opus
        codec = get_opus_codec(16000, 1, 60)
        opus_frames = codec.encode_multiple(pcm_data)
        
        logger.debug(f"  {audio_path.name}: {len(opus_frames)} Opus frames")
        return opus_frames
        
    except Exception as e:
        logger.error(f"Failed to load test audio {audio_path.name}: {e}")
        raise


async def load_audio_files_from_directory(audio_dir: Path) -> dict[str, list[bytes]]:
    """
    Load all WAV files from a directory and convert to Opus frames.
    
    Args:
        audio_dir: Path to directory containing WAV files
        
    Returns:
        Dictionary mapping filename to Opus frames list
    """
    logger.info(f"Loading audio files from: {audio_dir}")
    
    # Find all .wav files
    wav_files = sorted(audio_dir.glob("*.wav"))
    
    if not wav_files:
        raise ValueError(f"No .wav files found in {audio_dir}")
    
    logger.info(f"Found {len(wav_files)} WAV files")
    
    # Load all files
    audio_dict = {}
    for wav_file in wav_files:
        opus_frames = await load_test_audio(wav_file)
        audio_dict[wav_file.name] = opus_frames
    
    logger.success(f"Loaded {len(audio_dict)} audio files successfully")
    return audio_dict

audio_text_dict = {
    "test1.wav":"介绍一下你自己",
    "test2.wav":"天空为什么是蓝色的？地球为什么那么圆？",
    "test3.wav":"帮我算一下一道数学题，一加1等于几呀，2加2等于几呀？3加3等于几呀？",
    "test4.wav":"中国的首都是哪里，法国的首都是哪里，美国的首都是哪里？"
}


async def run_concurrent_test(
    server_url: str,
    num_clients: int,
    audio_files: dict[str, list[bytes]],
    interrupt_probability: float = 0.1,
    disconnect_probability: float = 0.05,
):
    """
    Run concurrent client test.
    
    Args:
        server_url: Server WebSocket URL
        num_clients: Number of concurrent clients
        audio_files: Dictionary of audio filename -> Opus frames
        interrupt_probability: Probability of interruption
        disconnect_probability: Probability of random disconnect
    """
    logger.info(f"Starting concurrent test with {num_clients} clients")
    logger.info(f"Available audio files: {len(audio_files)}")
    logger.info(f"Interrupt probability: {interrupt_probability}")
    logger.info(f"Disconnect probability: {disconnect_probability}")
    logger.info("=" * 60)
    
    # Get list of audio files for random assignment
    audio_list = list(audio_files.items())  # [(filename, opus_frames), ...]
    
    # Create clients with sequentially assigned audio
    clients = []
    for i in range(num_clients):
        # Select audio file in sequence
        audio_name, test_audio_opus = audio_list[i % len(audio_list)]
        
        client = ConcurrentTestClient(
            client_id=str(i),
            server_url=server_url,
            test_audio_opus=test_audio_opus,
            interrupt_probability=interrupt_probability,
            disconnect_probability=disconnect_probability,
        )
        clients.append(client)
        
        logger.info(f"Client-{i}: assigned audio '{audio_text_dict[audio_name]}' ({len(test_audio_opus)} frames)")
    
    logger.info("=" * 60)
    
    # Run all clients concurrently
    start_time = time.time()
    tasks = [client.run_full_test_cycle() for client in clients]
    await asyncio.gather(*tasks)
    duration = time.time() - start_time
    
    # Collect statistics
    logger.info("=" * 60)
    logger.info("Test completed!")
    logger.info(f"Total duration: {duration:.2f}s")
    logger.info("")
    
    # Aggregate stats
    total_stats = {
        "connections": 0,
        "disconnections": 0,
        "messages_sent": 0,
        "messages_received": 0,
        "audio_frames_sent": 0,
        "audio_bytes_received": 0,
        "text_messages_sent": 0,
        "text_messages_received": 0,
        "interruptions": 0,
        "random_disconnects": 0,
        "errors": 0,
        "total_audio_chunks_played": 0,
    }
    
    for client in clients:
        stats = client.get_stats()
        for key in total_stats:
            if key == "total_audio_chunks_played":
                total_stats[key] += stats["player"]["audio_chunks"]
            elif key in stats:
                total_stats[key] += stats[key]
    
    # Print statistics
    logger.info("Aggregated Statistics:")
    logger.info(f"  Connections: {total_stats['connections']}")
    logger.info(f"  Disconnections: {total_stats['disconnections']}")
    logger.info(f"  Messages sent: {total_stats['messages_sent']}")
    logger.info(f"  Messages received: {total_stats['messages_received']}")
    logger.info(f"  Audio frames sent: {total_stats['audio_frames_sent']}")
    logger.info(f"  Audio bytes received: {total_stats['audio_bytes_received']}")
    logger.info(f"  Audio chunks played: {total_stats['total_audio_chunks_played']}")
    logger.info(f"  Text messages sent: {total_stats['text_messages_sent']}")
    logger.info(f"  Text messages received: {total_stats['text_messages_received']}")
    logger.info(f"  Interruptions: {total_stats['interruptions']}")
    logger.info(f"  Random disconnects: {total_stats['random_disconnects']}")
    logger.info(f"  Errors: {total_stats['errors']}")
    logger.info("")
    
    # Print individual client stats
    logger.info("Individual Client Statistics:")
    for client in clients:
        stats = client.get_stats()
        logger.info(
            f"  Client-{client.client_id}: "
            f"sent={stats['messages_sent']}, "
            f"recv={stats['messages_received']}, "
            f"audio_frames={stats['audio_frames_sent']}, "
            f"interrupts={stats['interruptions']}, "
            f"errors={stats['errors']}"
        )
    
    logger.info("=" * 60)
    
    if total_stats['errors'] > 0:
        logger.warning(f"Test completed with {total_stats['errors']} errors")
    else:
        logger.success("Test completed successfully! ✓")

from vixio.utils.network import get_local_ip
ip_address = get_local_ip()
server_url = f"ws://{ip_address}:8000/xiaozhi/v1/"

async def main():
    """Main function."""
    import argparse
    
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Concurrent integration test for Xiaozhi server"
    )

    parser.add_argument(
        "--url",
        default=server_url,
        help="Server WebSocket URL",
    )
    parser.add_argument(
        "--clients",
        "-c",
        type=int,
        default=5,
        help="Number of concurrent clients (default: 5)",
    )
    parser.add_argument(
        "--audio-dir",
        default=str(Path(__file__).parent / "test_audios"),
        help="Path to directory containing test WAV files (default: test_audios in same directory)",
    )
    parser.add_argument(
        "--interrupt-prob",
        type=float,
        default=0.1,
        help="Probability of random interrupt (0.0-1.0, default: 0.1)",
    )
    parser.add_argument(
        "--disconnect-prob",
        type=float,
        default=0.05,
        help="Probability of random disconnect (0.0-1.0, default: 0.05)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )
    
    args = parser.parse_args()
    
    # Setup logger
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=args.log_level,
    )
    
    # Validate arguments
    if not 0 <= args.interrupt_prob <= 1:
        logger.error("Interrupt probability must be between 0 and 1")
        sys.exit(1)
    
    if not 0 <= args.disconnect_prob <= 1:
        logger.error("Disconnect probability must be between 0 and 1")
        sys.exit(1)
    
    audio_dir = Path(args.audio_dir)
    if not audio_dir.exists():
        logger.error(f"Audio directory not found: {audio_dir}")
        sys.exit(1)
    
    if not audio_dir.is_dir():
        logger.error(f"Path is not a directory: {audio_dir}")
        sys.exit(1)
    
    try:
        # Load all test audio files from directory
        audio_files = await load_audio_files_from_directory(audio_dir)
        
        # Run concurrent test
        await run_concurrent_test(
            server_url=args.url,
            num_clients=args.clients,
            audio_files=audio_files,
            interrupt_probability=args.interrupt_prob,
            disconnect_probability=args.disconnect_prob,
        )
        
    except KeyboardInterrupt:
        logger.warning("Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

