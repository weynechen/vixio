"""
Simple rule-based Chinese sentence aggregator provider

Enhanced rules:
- Punctuation detection (。！？；.!?;)
- Conjunction detection (或者、并且、但是、因为、所以...)
- Punctuation pairing (quotes, brackets)
- Minimum sentence length
- Special character filtering
"""

import re
from typing import List, Dict, Any, Set
from vixio.providers.sentence_aggregator.base import SentenceAggregatorProvider
from vixio.providers.registry import register_provider


@register_provider("sentence-aggregator-simple-cn")
class SimpleSentenceAggregatorProviderCN(SentenceAggregatorProvider):
    """
    Simple rule-based Chinese sentence aggregator.
    
    Uses enhanced regex patterns and heuristic rules to detect sentence boundaries.
    Suitable for real-time scenarios with low latency requirements.
    """
    
    # Chinese sentence ending punctuation
    # 。！？；.!?; and ellipsis (... …)
    SENTENCE_ENDINGS = r'[。！？；.!?;]|\.{3}|…'
    
    # Pattern to detect sentence boundary
    # Sentence ends with punctuation, optionally followed by quotes/brackets
    SENTENCE_PATTERN = re.compile(
        f'({SENTENCE_ENDINGS})(["\']?[）】」』]?)',
        re.UNICODE
    )
    
    # Chinese conjunctions that indicate incomplete sentence
    # If sentence starts with these, it's likely a continuation
    CONJUNCTIONS: Set[str] = {
        # Coordination conjunctions
        '或者', '或', '并且', '并', '而且', '以及', '和',
        # Adversative conjunctions
        '但是', '但', '可是', '然而', '不过', '却',
        # Causal conjunctions
        '因为', '由于', '所以', '因此', '因而',
        # Conditional conjunctions
        '如果', '假如', '要是', '倘若',
        # Other connectors
        '那么', '则', '就', '于是', '接着', '然后',
    }
    
    # Opening-closing punctuation pairs
    PUNCTUATION_PAIRS = {
        '"': '"',
        '"': '"',
        "'": "'",
        '(': ')',
        '（': '）',
        '[': ']',
        '【': '】',
        '{': '}',
        '「': '」',
        '『': '』',
        '《': '》',
    }
    
    # Special starting characters that indicate incomplete sentence
    INCOMPLETE_STARTS = {',', '，', '、', '；', ';', ':', '：'}
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "min_sentence_length": {
                "type": "int",
                "default": 5,
                "description": "Minimum characters to consider a valid sentence"
            },
            "enable_conjunction_check": {
                "type": "bool",
                "default": True,
                "description": "Check if sentence starts with conjunction (indicates continuation)"
            },
            "enable_punctuation_pairing": {
                "type": "bool",
                "default": True,
                "description": "Check if quotes/brackets are properly paired"
            },
            "enable_incomplete_start_check": {
                "type": "bool",
                "default": True,
                "description": "Check if sentence starts with comma/semicolon (indicates continuation)"
            },
        }
    
    def __init__(
        self,
        min_sentence_length: int = 5,
        enable_conjunction_check: bool = True,
        enable_punctuation_pairing: bool = True,
        enable_incomplete_start_check: bool = True,
    ):
        """
        Initialize simple Chinese sentence aggregator.
        
        Args:
            min_sentence_length: Minimum characters to consider a valid sentence
            enable_conjunction_check: Enable conjunction detection
            enable_punctuation_pairing: Enable punctuation pairing check
            enable_incomplete_start_check: Enable incomplete start check
        """
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.min_sentence_length = min_sentence_length
        self.enable_conjunction_check = enable_conjunction_check
        self.enable_punctuation_pairing = enable_punctuation_pairing
        self.enable_incomplete_start_check = enable_incomplete_start_check
        
        # Internal buffer
        self.buffer = ""
        
        self.logger.info(
            f"Initialized simple CN aggregator: "
            f"min_length={min_sentence_length}, "
            f"conjunction_check={enable_conjunction_check}, "
            f"punctuation_pairing={enable_punctuation_pairing}, "
            f"incomplete_start_check={enable_incomplete_start_check}"
        )
    
    def add_chunk(self, chunk: str) -> List[str]:
        """
        Add text chunk and extract complete sentences.
        
        Args:
            chunk: New text chunk (delta)
            
        Returns:
            List of complete sentences
        """
        self.buffer += chunk
        sentences = []
        
        # Find all sentence boundaries
        matches = list(self.SENTENCE_PATTERN.finditer(self.buffer))
        
        if not matches:
            # No complete sentence yet
            return []
        
        # Extract potential sentences
        last_end = 0
        pending_sentence = ""
        
        for match in matches:
            sentence_end = match.end()
            sentence = self.buffer[last_end:sentence_end].strip()
            
            # Merge with pending sentence if exists
            if pending_sentence:
                sentence = pending_sentence + sentence
                pending_sentence = ""
            
            # Validate sentence
            if self._is_valid_sentence(sentence):
                sentences.append(sentence)
                last_end = sentence_end
            else:
                # Keep this sentence as pending, might merge with next
                pending_sentence = sentence
                last_end = sentence_end
        
        # Update buffer with remaining text
        remaining = self.buffer[last_end:].strip()
        
        # If we have a pending sentence, prepend it to remaining buffer
        if pending_sentence:
            self.buffer = pending_sentence + " " + remaining if remaining else pending_sentence
        else:
            self.buffer = remaining
        
        return sentences
    
    def flush(self) -> str:
        """
        Get remaining text in buffer as final sentence.
        
        Returns:
            Remaining text (may not end with punctuation)
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        
        # Return remaining text if it meets minimum length
        # At flush time, we're less strict about validation
        if len(remaining) >= self.min_sentence_length:
            return remaining
        
        return ""
    
    def reset(self) -> None:
        """Reset buffer for new conversation."""
        self.buffer = ""
        self.logger.debug("Buffer reset")
    
    def _is_valid_sentence(self, sentence: str) -> bool:
        """
        Validate if sentence is complete and should be emitted.
        
        Args:
            sentence: Candidate sentence
            
        Returns:
            True if sentence is valid and complete
        """
        # Check minimum length
        if len(sentence) < self.min_sentence_length:
            return False
        
        # Check if starts with incomplete indicators
        if self.enable_incomplete_start_check:
            if self._has_incomplete_start(sentence):
                self.logger.debug(f"Incomplete start detected: '{sentence[:20]}...'")
                return False
        
        # Check if starts with conjunction
        if self.enable_conjunction_check:
            if self._starts_with_conjunction(sentence):
                self.logger.debug(f"Conjunction start detected: '{sentence[:20]}...'")
                return False
        
        # Check punctuation pairing
        if self.enable_punctuation_pairing:
            if not self._has_paired_punctuation(sentence):
                self.logger.debug(f"Unpaired punctuation detected: '{sentence[:20]}...'")
                return False
        
        return True
    
    def _has_incomplete_start(self, sentence: str) -> bool:
        """Check if sentence starts with comma/semicolon (incomplete)"""
        if not sentence:
            return False
        
        first_char = sentence[0]
        return first_char in self.INCOMPLETE_STARTS
    
    def _starts_with_conjunction(self, sentence: str) -> bool:
        """Check if sentence starts with conjunction"""
        if not sentence:
            return False
        
        # Check if sentence starts with any conjunction
        for conj in self.CONJUNCTIONS:
            if sentence.startswith(conj):
                return True
        
        return False
    
    def _has_paired_punctuation(self, sentence: str) -> bool:
        """
        Check if all opening punctuation has closing pair.
        
        Args:
            sentence: Text to check
            
        Returns:
            True if all punctuation is properly paired
        """
        # Count opening and closing punctuation
        for opening, closing in self.PUNCTUATION_PAIRS.items():
            open_count = sentence.count(opening)
            close_count = sentence.count(closing)
            
            # If we have unpaired opening punctuation, it's incomplete
            if open_count > close_count:
                return False
        
        return True
