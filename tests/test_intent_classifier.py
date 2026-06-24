import pytest
from src.agents.intent_classifier import classify_intent, IntentType

@pytest.mark.asyncio
async def test_classify_intent_navigational():
    assert await classify_intent("Kensara login") == IntentType.NAVIGATIONAL
    assert await classify_intent("kensaraai contact") == IntentType.NAVIGATIONAL

@pytest.mark.asyncio
async def test_classify_intent_commercial():
    assert await classify_intent("best DPDPA compliance software") == IntentType.COMMERCIAL
    assert await classify_intent("OneTrust alternative India") == IntentType.COMMERCIAL
    assert await classify_intent("DPDPA platform vs OneTrust") == IntentType.COMMERCIAL

@pytest.mark.asyncio
async def test_classify_intent_transactional():
    assert await classify_intent("DPDPA compliance consultant cost") == IntentType.TRANSACTIONAL
    assert await classify_intent("hire DPO India") == IntentType.TRANSACTIONAL
    assert await classify_intent("book privacy assessment") == IntentType.TRANSACTIONAL

@pytest.mark.asyncio
async def test_classify_intent_informational():
    assert await classify_intent("what is DPDPA") == IntentType.INFORMATIONAL
    assert await classify_intent("DPDP Act 2023 explained") == IntentType.INFORMATIONAL
    # Default case
    assert await classify_intent("data mapping obligations") == IntentType.INFORMATIONAL
