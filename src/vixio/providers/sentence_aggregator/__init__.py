"""
Sentence aggregator providers
"""

from vixio.providers.sentence_aggregator.base import SentenceAggregatorProvider
from vixio.providers.sentence_aggregator.simple_provider_cn import SimpleSentenceAggregatorProviderCN

__all__ = [
    'SentenceAggregatorProvider',
    'SimpleSentenceAggregatorProviderCN',
]
