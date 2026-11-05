"""Conversational response language. English by default, Roman Urdu only for Roman-Urdu notes."""
import re

_STRONG_ROMAN = re.compile(r"(?i)\b(?:nay|diay|diye|diya|liay|liye|liya|udhaar|bheja|bhejay|bheje|kharcha|khareeda|kharida|mujhay|mujhe|apnay|apni|aap|hain|hai|hy|kya|nahi|kahan|kaunsa|kitna|kitni|lagay|kiay)\b")
_ROMAN_WORDS = re.compile(r"(?i)\b(?:se|ko|ki|ka|ke|aur|main|mein|mai|mera|meri|nay|baji|bhai|sab|sari|saray|diya|diay|liay|hy|hai)\b")

def reply_language(text: str) -> str:
    """No model call or paid language detector; conservative default is English."""
    if _STRONG_ROMAN.search(text) or len(_ROMAN_WORDS.findall(text)) >= 2:
        return 'roman_urdu'
    return 'en'


def choose(plan: dict, english: str, roman_urdu: str) -> str:
    return roman_urdu if plan.get('reply_language') == 'roman_urdu' else english
