"""Small conservative Unicode date-evidence gate, NOT a full multilingual tokenizer.

LLM outputs semantic ISO dates. This module checks that the original note
contains an actual date-like expression before trusting such suggestions.
"""
from __future__ import annotations
import re
import unicodedata

_MONTHS = (
    'جنوری', 'فروری', 'مارچ', 'اپریل', 'مئی', 'جون', 'جولائی', 'اگست', 'ستمبر', 'ستمبر', 'اکتوبر', 'نومبر', 'دسمبر',
    'जनवरी', 'फ़रवरी', 'फरवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'सितम्बर', 'अक्टूबर', 'नवंबर', 'दिसंबर',
    'জানুয়ারি', 'ফেব্রুয়ারি', 'মার্চ', 'এপ্রিল', 'মে', 'জুন', 'জুলাই', 'আগস্ট', 'সেপ্টেম্বর', 'অক্টোবর', 'নভেম্বর', 'ডিসেম্বর',
    'جنوري', 'فيبروري', 'مارچ', 'اپريل', 'مئي', 'جون', 'جولاءِ', 'آگسٽ', 'سيپٽمبر', 'آڪٽوبر', 'نومبر', 'ڊسمبر',
    'يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر',
    '一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月',
)
_MONTH_RE = re.compile('|'.join(re.escape(w) for w in sorted(set(_MONTHS), key=len, reverse=True)), re.IGNORECASE)
_NUMBER = r'[0-9٠-٩۰-۹०-९০-৯]{1,4}'
_ORDINAL = r'(?:یکم|پہلی|پہلا|اول|पहली|पहला|প্রথম)'
_CHINESE = re.compile(r'(?:[0-9０-９一二三四五六七八九十]{1,4}年)?[0-9０-９一二三四五六七八九十]{1,2}月[0-9０-９一二三四五六七八九十]{1,3}日')

def has_explicit_date_evidence(text: str) -> bool:
    if _CHINESE.search(text):
        return True
    normalized = ''.join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in unicodedata.normalize('NFKC', text))
    # Explicit month plus day/ordinal; do not accept month-only or year-only notes.
    if _MONTH_RE.search(normalized) and re.search(r'(?<!\d)20\d{2}(?!\d)', normalized):
        has_ordinal = re.search(_ORDINAL, normalized) is not None
        has_day = any(1 <= int(token) <= 31 for token in re.findall(r'(?<!\d)\d+(?!\d)', normalized))
        if has_ordinal or has_day:
            return True
    return False
