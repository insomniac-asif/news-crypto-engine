"""NLP processing pipeline — text cleaning, entity extraction, event classification, sentiment."""

from src.processing.entity_extractor import EntityExtractor
from src.processing.event_classifier import ClassifiedEvent, EventClassifier
from src.processing.sentiment import SentimentAnalyzer
from src.processing.text_cleaner import clean_article, clean_html

__all__ = [
    "EntityExtractor",
    "EventClassifier",
    "ClassifiedEvent",
    "SentimentAnalyzer",
    "clean_article",
    "clean_html",
]
