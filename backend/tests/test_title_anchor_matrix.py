"""Universal title-anchor matrix: presentation wrappers must not become media anchors."""
import unittest

from app.media_relevance import build_media_topic_context

MATRIX = [
    ("general_explainer", "Why the Internet Changed Modern Communication", "Internet"),
    ("tech_science", "How Quantum Computers Process Information", "Quantum Computers Process Information"),
    ("what_if", "What If Earth Suddenly Stopped Rotating?", "Earth Suddenly Stopped Rotating"),
    ("historical_place", "Inside the Colosseum: Engineering of Ancient Rome", "Colosseum"),
    ("historical_event", "How the Roman Empire Collapsed", "Roman Empire Collapsed"),
    ("geography_travel", "Why Japan Has So Many Earthquakes", "Japan"),
    ("nature_wildlife", "How Wolves Hunt in Yellowstone", "Wolves Hunt in Yellowstone"),
    ("space_astronomy", "How Mars Lost Its Atmosphere", "Mars Lost Its Atmosphere"),
    ("business_economics", "Why Apple Became a Trillion-Dollar Company", "Apple"),
    ("educational_how_it_works", "How Elevators Work", "Elevators"),
    ("health_wellness", "How Insulin Controls Blood Sugar", "Insulin Controls Blood Sugar"),
    ("sports", "How Formula 1 Cars Generate Downforce", "Formula 1 Cars Generate Downforce"),
    ("automotive_transport", "How Electric Cars Convert Battery Power Into Motion",
     "Electric Cars Convert Battery Power Into Motion"),
    ("food_cooking", "Why Bread Dough Rises", "Bread Dough Rises"),
    ("environment_climate", "How Wind Turbines Generate Electricity", "Wind Turbines Generate Electricity"),
    ("culture_society", "Why the Japanese Tea Ceremony Matters", "Japanese Tea Ceremony Matters"),
    ("architecture_engineering", "How Suspension Bridges Carry Massive Loads", "Suspension Bridges"),
    ("historical_event", "The Dark Secrets Behind Ancient Egyptian Mummies", "Ancient Egyptian Mummies"),
    ("space_astronomy", "What Really Happens Inside a Black Hole", "Black Hole"),
    ("automotive_transport", "How Nuclear Submarines Stay Hidden Underwater",
     "Nuclear Submarines Stay Hidden Underwater"),
    ("general_explainer", "The Rise and Fall of Blockbuster Video", "Blockbuster Video"),
    ("tech_science", "Inside a Fusion Reactor: From Plasma to Power", "Fusion Reactor"),
    ("geography_travel", "10 Things You Didn't Know About Iceland", "Iceland"),
    ("business_economics", "Tesla Explained: How the Company Makes Money", "Tesla"),
    ("historical_event", "What Happened During the Great Fire of London?", "Great Fire of London"),
    ("general_explainer", "Who Was Ada Lovelace?", "Ada Lovelace"),
    ("health_wellness", "The Truth About Sugar", "Sugar"),
    ("general_explainer", "Everything You Need to Know About Bitcoin", "Bitcoin"),
    ("tech_science", "5G Explained - How It Really Works", "5G"),
]


class TitleAnchorMatrixTests(unittest.TestCase):
    def test_primary_topic_matrix(self):
        for profile, title, expected in MATRIX:
            with self.subTest(title=title):
                self.assertEqual(build_media_topic_context(title, profile).primary_topic, expected)

    def test_editorial_words_never_anchor(self):
        context = build_media_topic_context("The Dark Secrets Behind Ancient Egyptian Mummies", "historical_event")
        self.assertEqual(context.required_anchor_phrases, ("ancient egyptian mummies",))
        self.assertNotIn("dark", context.required_anchor_phrases[0])
        # Stripped words remain available as soft context, never as the anchor.
        self.assertIn("secrets", context.optional_context_tokens)

    def test_wrapper_only_title_keeps_something(self):
        self.assertTrue(build_media_topic_context("The Truth About", "general_explainer").primary_topic)
        self.assertTrue(build_media_topic_context("", "general_explainer").primary_topic == "")
