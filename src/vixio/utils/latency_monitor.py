"""
Latency monitoring for voice AI pipeline

Tracks timing of key events to measure first-response latency

Log Mode Control:
    Set environment variable VIXIO_LOG_MODE=file to enable latency JSON logging.
    When not set or set to 'none', latency data is only kept in memory.
"""

import time
from typing import Dict, Optional, Any
from loguru import logger
import os

from vixio.utils.logger_config import is_file_logging_enabled


class LatencyMonitor:
    """
    Track latency for voice AI pipeline, focusing on first response time.
    
    Key Timeline:
    T0: user_speech_end (VAD_END received)
    T1: turn_end_detected (TURN_END emitted after silence)
    T2: asr_complete (ASR transcription done)
    T3: agent_first_token (Agent first token, TTFT)
    T4: first_sentence_complete (First complete sentence)
    T5: tts_first_audio_ready (TTS first audio chunk generated)
    T6: first_audio_sent (First audio chunk sent to client)
    
    Latencies:
    - turn_detection: T1 - T0
    - asr: T2 - T1
    - agent_ttft: T3 - T2 (Time To First Token)
    - agent_sentence: T4 - T2
    - tts: T5 - T4
    - network: T6 - T5
    - total_first_response: T6 - T0 (MOST IMPORTANT)
    """
    
    def __init__(self, log_dir: str = "logs"):
        """
        Initialize latency monitor.
        
        Args:
            log_dir: Directory for latency logs
        """
        self.sessions: Dict[str, Dict[int, Dict[str, float]]] = {}
        self.log_dir = log_dir
        self._handler_id: Optional[int] = None
        self._file_logging_enabled = is_file_logging_enabled()
        
        # Only create log directory and handler if file logging is enabled
        if self._file_logging_enabled:
            os.makedirs(log_dir, exist_ok=True)
            self._ensure_handler()
    
    def _check_handler_exists(self) -> bool:
        """
        Check if the latency handler still exists.
        
        Returns:
            True if handler exists, False otherwise
        """
        if self._handler_id is None:
            return False
        
        # Check if handler ID is still valid
        try:
            # Try to access handler by checking logger's handlers
            # This is a workaround since loguru doesn't provide direct API
            # to check if a handler ID is valid
            from loguru._logger import Logger
            if hasattr(logger, '_core'):
                handlers = logger._core.handlers
                return self._handler_id in handlers
        except Exception:
            pass
        
        return False
    
    def _ensure_handler(self) -> None:
        """
        Ensure latency JSON handler is added to logger.
        
        This method is idempotent - it only adds the handler if it doesn't exist.
        This handles the case where logger.remove() is called elsewhere (e.g., in logger_config).
        Only creates handler if file logging is enabled.
        """
        # Skip if file logging is disabled
        if not self._file_logging_enabled:
            return
        
        # Check if handler already exists and is valid
        if self._check_handler_exists():
            return
        
        # Add or re-add the handler
        log_file = os.path.join(self.log_dir, "latency_{time:YYYY-MM-DD}.json")
        self._handler_id = logger.add(
            log_file,
            rotation="00:00",  # Rotate daily
            retention="30 days",
            format="{message}",
            level="INFO",
            filter=lambda record: "latency_report" in record["extra"],
            serialize=True,  # Output as JSON
        )
        logger.debug(f"Latency JSON handler added/re-added with ID: {self._handler_id}")
    
    def record(
        self,
        session_id: str,
        turn_id: int,
        event: str,
        timestamp: Optional[float] = None
    ) -> None:
        """
        Record timestamp for an event.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            event: Event name (e.g., "user_speech_end", "asr_complete")
            timestamp: Event timestamp (defaults to current time)
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Initialize session if needed
        if session_id not in self.sessions:
            self.sessions[session_id] = {}
        
        # Initialize turn if needed
        if turn_id not in self.sessions[session_id]:
            self.sessions[session_id][turn_id] = {}
        
        # Record timestamp
        self.sessions[session_id][turn_id][event] = timestamp
    
    def get_timestamps(self, session_id: str, turn_id: int) -> Dict[str, float]:
        """
        Get all timestamps for a turn.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            
        Returns:
            Dictionary of event timestamps
        """
        if session_id not in self.sessions:
            return {}
        if turn_id not in self.sessions[session_id]:
            return {}
        return self.sessions[session_id][turn_id].copy()
    
    def calculate_latencies(
        self,
        session_id: str,
        turn_id: int
    ) -> Optional[Dict[str, float]]:
        """
        Calculate latencies for a turn.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            
        Returns:
            Dictionary of latencies in milliseconds, or None if incomplete
        """
        timestamps = self.get_timestamps(session_id, turn_id)
        
        if not timestamps:
            return None
        
        # Check if we have minimum required timestamps
        if "user_speech_end" not in timestamps:
            return None
        
        latencies = {}
        t0 = timestamps.get("user_speech_end")
        
        # Turn detection latency
        if "turn_end_detected" in timestamps:
            t1 = timestamps["turn_end_detected"]
            latencies["turn_detection_ms"] = round((t1 - t0) * 1000, 2)
        else:
            t1 = t0
        
        # ASR latency
        if "asr_complete" in timestamps:
            t2 = timestamps["asr_complete"]
            latencies["asr_ms"] = round((t2 - t1) * 1000, 2)
        else:
            t2 = t1
        
        # Agent TTFT (Time To First Token) - most critical for LLM
        # network latency and llm inference latency
        if "agent_first_token" in timestamps:
            t3 = timestamps["agent_first_token"]
            latencies["agent_ttft_ms"] = round((t3 - t2) * 1000, 2)
        
        # Agent first sentence complete
        # network latency and sentence length
        if "first_sentence_complete" in timestamps:
            t4 = timestamps["first_sentence_complete"]
            latencies["agent_sentence_ms"] = round((t4 - t2) * 1000, 2)
        else:
            t4 = t2
        
        # TTS latency (first audio generation)
        if "tts_first_audio_ready" in timestamps:
            t5 = timestamps["tts_first_audio_ready"]
            latencies["tts_ms"] = round((t5 - t4) * 1000, 2)
        else:
            t5 = t4
        
        # Network send latency
        if "first_audio_sent" in timestamps:
            t6 = timestamps["first_audio_sent"]
            latencies["network_ms"] = round((t6 - t5) * 1000, 2)
            
            # Total first response latency (E2E)
            latencies["total_first_response_ms"] = round((t6 - t0) * 1000, 2)
        
        return latencies if latencies else None
    
    def log_report(
        self,
        session_id: str,
        turn_id: int,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log latency report for a turn (JSON format via loguru).
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            extra_data: Additional data to include in report
        """
        # Ensure handler exists before logging (in case logger.remove() was called)
        self._ensure_handler()
        
        timestamps = self.get_timestamps(session_id, turn_id)
        latencies = self.calculate_latencies(session_id, turn_id)
        
        if not latencies:
            return
        
        # Build report
        report = {
            "session_id": session_id,
            "turn_id": turn_id,
            "latencies": latencies,
            "timestamps": timestamps,
        }
        
        # Add extra data if provided
        if extra_data:
            report["extra"] = extra_data
        
        # Identify bottlenecks (>1000ms)
        bottlenecks = []
        for key, value in latencies.items():
            if value > 1000 and key != "agent_sentence_ms":  # Sentence time is cumulative
                bottlenecks.append(f"{key}={value}ms")
        
        if bottlenecks:
            report["bottlenecks"] = bottlenecks
        
        # Log with special marker for filtering
        logger.bind(latency_report=True).info(
            f"Latency Report - Session: {session_id[:8]}..., Turn: {turn_id}, "
            f"Total: {latencies.get('total_first_response_ms', 'N/A')}ms",
            extra={"report": report}
        )
    
    def print_summary(
        self,
        session_id: str,
        turn_id: int
    ) -> None:
        """
        Print human-readable latency summary to console.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
        """
        latencies = self.calculate_latencies(session_id, turn_id)
        
        if not latencies:
            logger.warning(f"No latency data for session {session_id[:8]}, turn {turn_id}")
            return
        
        # Build formatted output
        session_short = session_id[:8] if len(session_id) > 8 else session_id
        lines = [
            f"\n{'='*60}",
            f"Latency Report - Session: {session_short}, Turn: {turn_id}",
            f"{'='*60}",
        ]
        
        # Add each latency with visual indicator
        if "turn_detection_ms" in latencies:
            lines.append(f"├─ Turn Detection:    {latencies['turn_detection_ms']:>7.0f}ms")
        
        if "asr_ms" in latencies:
            lines.append(f"├─ ASR:               {latencies['asr_ms']:>7.0f}ms")
        
        if "agent_ttft_ms" in latencies:
            ttft = latencies['agent_ttft_ms']
            indicator = " ⚠️" if ttft > 1000 else ""
            lines.append(f"├─ Agent TTFT:        {ttft:>7.0f}ms{indicator}")
        
        if "agent_sentence_ms" in latencies:
            lines.append(f"├─ Agent 1st Sent:    {latencies['agent_sentence_ms']:>7.0f}ms")
        
        if "tts_ms" in latencies:
            lines.append(f"├─ TTS:               {latencies['tts_ms']:>7.0f}ms")
        
        if "network_ms" in latencies:
            lines.append(f"├─ Network:           {latencies['network_ms']:>7.0f}ms")
        
        if "total_first_response_ms" in latencies:
            total = latencies['total_first_response_ms']
            indicator = " ⚠️" if total > 3000 else ""
            lines.append(f"└─ Total E2E:         {total:>7.0f}ms{indicator}")
        
        lines.append(f"{'='*60}\n")
        
        # Print to console
        print("\n".join(lines))
    
    def clear_session(self, session_id: str) -> None:
        """
        Clear data for a session.
        
        Args:
            session_id: Session identifier
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    def clear_turn(self, session_id: str, turn_id: int) -> None:
        """
        Clear data for a specific turn.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
        """
        if session_id in self.sessions and turn_id in self.sessions[session_id]:
            del self.sessions[session_id][turn_id]


# Global singleton instance
_global_monitor: Optional[LatencyMonitor] = None


def get_latency_monitor(log_dir: str = "logs") -> LatencyMonitor:
    """
    Get global latency monitor instance.
    
    Args:
        log_dir: Directory for latency logs
        
    Returns:
        Global LatencyMonitor instance
    """
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = LatencyMonitor(log_dir=log_dir)
    return _global_monitor

