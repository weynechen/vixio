"""
Test cases for sentence aggregator providers
"""

import pytest
from vixio.providers.sentence_aggregator import SimpleSentenceAggregatorProviderCN


def test_simple_cn_provider_creation():
    """Test provider creation and properties"""
    provider = SimpleSentenceAggregatorProviderCN(
        min_sentence_length=5,
        enable_conjunction_check=True,
        enable_punctuation_pairing=True,
        enable_incomplete_start_check=True,
    )
    
    assert provider.category == "sentence_aggregator"
    assert provider.is_local is True
    assert provider.is_stateful is True
    assert provider.name == "sentence-aggregator-simple-cn"


def test_normal_sentence_aggregation():
    """Test normal sentence boundary detection"""
    provider = SimpleSentenceAggregatorProviderCN(min_sentence_length=5)
    
    sentences = provider.add_chunk('你好，我想问一下。')
    assert len(sentences) == 1
    assert sentences[0] == '你好，我想问一下。'


def test_incomplete_start_detection():
    """Test detection of sentences starting with comma/semicolon"""
    provider = SimpleSentenceAggregatorProviderCN(
        min_sentence_length=5,
        enable_incomplete_start_check=True
    )
    
    # Sentence starting with comma should not be emitted
    sentences = provider.add_chunk('，建议联系技术支持获取帮助。')
    assert len(sentences) == 0
    assert provider.buffer == '，建议联系技术支持获取帮助。'


def test_conjunction_detection():
    """Test detection of sentences starting with conjunctions"""
    provider = SimpleSentenceAggregatorProviderCN(
        min_sentence_length=5,
        enable_conjunction_check=True
    )
    
    # Sentence starting with conjunction should not be emitted
    sentences = provider.add_chunk('或者想了解更多信息。')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('但是这样不太好。')
    assert len(sentences) == 0


def test_minimum_length_check():
    """Test minimum sentence length validation"""
    provider = SimpleSentenceAggregatorProviderCN(min_sentence_length=5)
    
    # Too short sentence should not be emitted
    sentences = provider.add_chunk('函数。')
    assert len(sentences) == 0


def test_streaming_aggregation():
    """Test realistic streaming scenario"""
    provider = SimpleSentenceAggregatorProviderCN(
        min_sentence_length=5,
        enable_conjunction_check=True,
        enable_incomplete_start_check=True
    )
    
    # Simulate streaming: "可以尝试重新登录一下，或者检查一下网络设置。"
    sentences = provider.add_chunk('可以尝试')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('重新登录')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('一下')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('，或者')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('检查一下')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('网络设置。')
    assert len(sentences) == 1
    assert sentences[0] == '可以尝试重新登录一下，或者检查一下网络设置。'


def test_punctuation_pairing():
    """Test punctuation pairing detection"""
    provider = SimpleSentenceAggregatorProviderCN(
        min_sentence_length=5,
        enable_punctuation_pairing=True
    )
    
    # Unpaired quotes should not be emitted
    sentences = provider.add_chunk('他说"你好')
    assert len(sentences) == 0
    
    sentences = provider.add_chunk('"。')
    assert len(sentences) == 1
    assert sentences[0] == '他说"你好"。'


def test_reset():
    """Test provider reset"""
    provider = SimpleSentenceAggregatorProviderCN(min_sentence_length=5)
    
    provider.add_chunk('测试内容')
    assert len(provider.buffer) > 0
    
    provider.reset()
    assert len(provider.buffer) == 0


def test_flush():
    """Test flushing remaining buffer"""
    provider = SimpleSentenceAggregatorProviderCN(min_sentence_length=5)
    
    provider.add_chunk('这是最后的内容')
    remaining = provider.flush()
    
    assert remaining == '这是最后的内容'
    assert len(provider.buffer) == 0


def test_multiple_sentences_streaming():
    """Test multiple sentences in streaming fashion"""
    provider = SimpleSentenceAggregatorProviderCN(min_sentence_length=5)
    
    # In real streaming, sentences come incrementally
    sentences = provider.add_chunk('你好啊同学。')
    assert len(sentences) == 1
    assert sentences[0] == '你好啊同学。'
    
    sentences = provider.add_chunk('我是小智助手。')
    assert len(sentences) == 1
    assert sentences[0] == '我是小智助手。'
    
    sentences = provider.add_chunk('很高兴认识你。')
    assert len(sentences) == 1
    assert sentences[0] == '很高兴认识你。'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
