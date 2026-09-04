"""Token-strength scoring: a shared adjective must never outrank the real subject."""
import unittest

from app.media_relevance import build_media_topic_context, score_media_candidate
from app.media_relevance import build_media_query_candidates, scene_context

# (profile, title, scene_query, better_metadata, worse_metadata)
RANKINGS = [
    ("historical_event", "The Dark Secrets Behind Ancient Egyptian Mummies",
     "ancient egyptian mummy sarcophagus archaeology",
     "Mummy sarcophagus museum Cairo", "Egyptian desert fox wildlife"),
    ("historical_event", "The Dark Secrets Behind Ancient Egyptian Mummies",
     "ancient egyptian mummy sarcophagus archaeology",
     "Golden sarcophagus Tutankhamun", "Ancient temple ruins Greece"),
    ("space_astronomy", "What Really Happens Inside a Black Hole",
     "black hole event horizon accretion disk",
     "Black hole accretion disk illustration", "Black cat sleeping"),
    ("nature_wildlife", "How Wolves Hunt in Yellowstone", "grey wolf pack hunting snow",
     "Wolf pack running snow", "Yellowstone geyser"),
    ("automotive_transport", "How Nuclear Submarines Stay Hidden Underwater",
     "nuclear submarine periscope sonar room",
     "Nuclear submarine surfacing ocean", "Underwater fish coral"),
    ("architecture_engineering", "How Suspension Bridges Carry Massive Loads",
     "suspension bridge cables tower",
     "Golden Gate suspension bridge", "Old stone bridge river"),
    ("business_economics", "Why Apple Became a Trillion-Dollar Company",
     "apple store iphone launch crowd",
     "Apple store glass cube", "Red apple fruit orchard"),
    ("culture_society", "Why the Japanese Tea Ceremony Matters",
     "japanese tea ceremony matcha bowl",
     "Tea ceremony kimono tatami", "Japanese street food Tokyo"),
    ("food_cooking", "Why Bread Dough Rises", "bread dough proofing yeast bubbles",
     "Bread dough rising bowl", "Yeast microscope"),
    ("health_wellness", "How Insulin Controls Blood Sugar", "insulin injection pen diabetes",
     "Insulin pen injection", "Sugar cubes bowl"),
]


class MediaScoringStrengthTests(unittest.TestCase):
    def test_real_subject_outranks_shared_adjective(self):
        for profile, title, query, better, worse in RANKINGS:
            with self.subTest(title=title, better=better, worse=worse):
                context = build_media_topic_context(title, profile)
                good = score_media_candidate(context, query, title=better)
                bad = score_media_candidate(context, query, title=worse)
                self.assertTrue(good.accepted)
                self.assertGreater(good.score, bad.score)

    def test_weak_modifier_alone_is_not_entity_match(self):
        context = build_media_topic_context("The Dark Secrets Behind Ancient Egyptian Mummies", "historical_event")
        result = score_media_candidate(context, "ancient egyptian mummy sarcophagus", title="Egyptian desert fox wildlife")
        self.assertTrue(result.accepted)  # soft: still usable as last resort
        self.assertFalse(result.entity_match)
        self.assertEqual(result.reason, "setting-only-b-roll")

    def test_single_noun_subject_stays_entity_match(self):
        context = build_media_topic_context("How Do Octopuses Change Color?", "nature_wildlife")
        result = score_media_candidate(context, "octopus chromatophore", title="Octopus swimming underwater")
        self.assertTrue(result.entity_match)
        self.assertGreaterEqual(result.score, 30)

    def test_irregular_and_es_plurals_match(self):
        wolves = build_media_topic_context("How Wolves Hunt", "nature_wildlife")
        self.assertTrue(score_media_candidate(wolves, "wolf pack", title="Grey wolf").entity_match)
        bridges = build_media_topic_context("How Suspension Bridges Work", "architecture_engineering")
        self.assertTrue(score_media_candidate(bridges, "bridge deck", title="Suspension bridge at dawn").entity_match)

    def test_all_weak_subject_still_anchors(self):
        context = build_media_topic_context("How the Ocean Works", "general_explainer")
        result = score_media_candidate(context, "ocean waves current", title="Ocean waves beach")
        self.assertTrue(result.entity_match)

    def test_no_new_hard_rejections(self):
        context = build_media_topic_context("How Nuclear Submarines Stay Hidden Underwater", "automotive_transport")
        for meta in ("Toy submarine in aquarium", "Underwater fish coral", "Navy sonar operator control room"):
            self.assertTrue(score_media_candidate(context, "nuclear submarine periscope sonar room", title=meta).accepted)
        unrelated = score_media_candidate(context, "nuclear submarine periscope", title="Golden retriever park")
        self.assertTrue(unrelated.accepted)
        self.assertEqual(unrelated.confidence, "bad")


class MediaTitleEvidenceTests(unittest.TestCase):
    def test_subject_in_title_beats_subject_only_in_tags(self):
        context = build_media_topic_context("The Dark Secrets Behind Ancient Egyptian Mummies", "historical_event")
        in_title = score_media_candidate(context, "mummy sarcophagus", title="Mummy sarcophagus museum")
        in_tags = score_media_candidate(context, "mummy sarcophagus", title="Desert landscape",
                                        tags=["mummy", "sand", "sun", "travel"])
        self.assertGreater(in_title.score, in_tags.score)
        self.assertTrue(in_tags.accepted)

    def test_file_name_noise_is_not_penalised(self):
        context = build_media_topic_context("Who Was Ada Lovelace?", "general_explainer")
        clean = score_media_candidate(context, "ada lovelace portrait", title="Ada Lovelace portrait")
        noisy = score_media_candidate(context, "ada lovelace portrait", title="File:Ada Lovelace portrait.jpg")
        self.assertEqual(clean.score, noisy.score)

    def test_foreign_title_words_only_mildly_penalised(self):
        context = build_media_topic_context("How Do Octopuses Change Color?", "nature_wildlife")
        plain = score_media_candidate(context, "octopus reef", title="Octopus")
        busy = score_media_candidate(context, "octopus reef", title="Octopus aquarium tourist family holiday photo")
        self.assertTrue(busy.entity_match)
        self.assertGreater(plain.score, busy.score)
        self.assertLessEqual(plain.score - busy.score, 6)


class PracticalHeadQueryTests(unittest.TestCase):
    def test_head_query_keeps_noun_phrase_and_drops_origin_words(self):
        from app.media_relevance import practical_head_query
        cases = [
            ("What Really Happens Inside a Black Hole", "space_astronomy", "black hole accretion disk", "black hole"),
            ("The Dark Secrets Behind Ancient Egyptian Mummies", "historical_event",
             "ancient egyptian mummy sarcophagus", "mummy"),
            ("Why the Japanese Tea Ceremony Matters", "culture_society", "japanese tea ceremony matcha", "tea ceremony"),
            ("How Do Octopuses Change Color?", "nature_wildlife", "octopus camouflage underwater", "octopus"),
            ("How Nuclear Submarines Stay Hidden Underwater", "automotive_transport",
             "nuclear submarine periscope", "nuclear submarine"),
        ]
        for title, profile, query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(practical_head_query(query, build_media_topic_context(title, profile)), expected)


class ProfileDomainHintTests(unittest.TestCase):
    def test_domain_word_breaks_word_sense_tie(self):
        mars = build_media_topic_context("How Mars Lost Its Atmosphere", "space_astronomy")
        planet = score_media_candidate(mars, "mars surface red rocks rover", title="Planet Mars from orbit")
        candy = score_media_candidate(mars, "mars surface red rocks rover", title="Mars chocolate bar")
        self.assertGreater(planet.score, candy.score)
        self.assertTrue(candy.accepted)  # still soft

    def test_hint_bonus_is_small_and_capped(self):
        context = build_media_topic_context("How Insulin Controls Blood Sugar", "health_wellness")
        plain = score_media_candidate(context, "insulin pen", title="Insulin pen")
        hinted = score_media_candidate(context, "insulin pen", title="Insulin pen patient doctor clinic hospital")
        self.assertLessEqual(hinted.score - plain.score, 6)

    def test_url_slug_noise_is_not_penalised(self):
        context = build_media_topic_context("How Do Octopuses Change Color?", "nature_wildlife")
        clean = score_media_candidate(context, "octopus reef", title="octopus on reef")
        slug = score_media_candidate(context, "octopus reef", title="https://www.pexels.com/video/octopus-on-reef-123/")
        self.assertEqual(clean.score, slug.score)


class SoftAnchorTests(unittest.TestCase):
    def test_concrete_scene_query_is_left_untouched(self):
        from app.media_relevance import validate_scene_query
        cases = [
            ("The Fall of the Berlin Wall", "historical_event", "Stasi headquarters Berlin 1989"),
            ("How Vaccines Train Your Immune System", "health_wellness", "T-cell lymphocyte"),
            ("Why Venice Is Sinking", "geography_travel", "wooden piles foundation"),
        ]
        for title, profile, query in cases:
            with self.subTest(query=query):
                result = validate_scene_query(query, build_media_topic_context(title, profile))
                self.assertEqual(result.final_query, query)
                self.assertFalse(result.repaired)

    def test_generic_or_empty_query_gets_short_subject_head(self):
        from app.media_relevance import validate_scene_query
        context = build_media_topic_context("The Fall of the Berlin Wall", "historical_event")
        repaired = validate_scene_query("detailed close view", context)
        self.assertTrue(repaired.repaired)
        self.assertEqual(repaired.final_query, "detailed close view berlin wall")
        self.assertNotIn("fall of", repaired.final_query)
        self.assertEqual(validate_scene_query("", context).final_query, "berlin wall")


class RichSceneUnderstandingTests(unittest.TestCase):
    def test_negative_meaning_guards_demote_cable_word_sense_traps(self):
        job = build_media_topic_context(
            "How Deep Sea Cables Carry the Internet", "educational_how_it_works"
        )
        scene = scene_context(
            job,
            "submarine fiber optic cable ocean floor",
            visual_subject="submarine fiber optic cable",
            positive_terms=("seabed", "internet data", "cable landing station"),
            negative_terms=("cable car", "cable-stayed bridge", "electrical cable"),
        )
        good = score_media_candidate(
            scene,
            scene.scene_query,
            title="Submarine fiber optic cable on ocean floor seabed",
        )
        cable_car = score_media_candidate(
            scene,
            scene.scene_query,
            title="Cable car climbing mountain",
        )
        bridge = score_media_candidate(
            scene,
            scene.scene_query,
            title="Cable-stayed bridge traffic city",
        )
        self.assertEqual(good.confidence, "strong")
        self.assertGreater(good.score, cable_car.score)
        self.assertGreater(good.score, bridge.score)
        self.assertTrue(any(value.startswith("negative:") for value in cable_car.penalties))
        self.assertTrue(any(value.startswith("negative:") for value in bridge.penalties))

    def test_scene_visual_subject_beats_title_on_concrete_cutaway(self):
        job = build_media_topic_context(
            "Why the Roman Aqueducts Still Stand", "architecture_engineering"
        )
        scene = scene_context(
            job,
            "lime mortar masonry mixing",
            visual_subject="lime mortar masonry",
            positive_terms=("mortar", "stone", "mixing"),
        )
        cutaway = score_media_candidate(
            scene, scene.scene_query, title="Lime mortar masonry mixing stone"
        )
        title_only = score_media_candidate(
            scene, scene.scene_query, title="Roman aqueduct ruins aerial view"
        )
        self.assertGreater(cutaway.score, title_only.score)
        self.assertTrue(cutaway.entity_match)

    def test_cutaway_query_variants_do_not_force_video_title_subject(self):
        job = build_media_topic_context(
            "Why the Roman Aqueducts Still Stand", "architecture_engineering"
        )
        scene = scene_context(
            job,
            "lime mortar masonry",
            visual_subject="lime mortar",
            search_variants=("historic lime mortar", "stone mortar mixing"),
        )
        variants = build_media_query_candidates(scene.scene_query, scene, limit=6)
        self.assertIn("lime mortar masonry", variants)
        self.assertIn("historic lime mortar", variants)
        self.assertFalse(any("aqueduct" in value.lower() for value in variants))


class SceneQueryScoringTests(unittest.TestCase):
    def test_candidate_from_broad_variant_is_judged_against_scene_query(self):
        from app.media_relevance import scene_context
        job = build_media_topic_context("The Fall of the Berlin Wall", "historical_event")
        scene = scene_context(job, "Berlin Wall construction 1961")
        anniversary = "File:35th anniversary of the fall of the Berlin Wall event 2024-11-09 04.jpg"
        # Search variant without the year would previously let this through.
        result = score_media_candidate(scene, "Fall of the Berlin Wall", title=anniversary)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "conflicting-named-date")

    def test_era_years_downrank_present_day_media_for_undated_scene(self):
        from app.media_relevance import scene_context, with_era_years
        job = with_era_years(build_media_topic_context("The Fall of the Berlin Wall", "historical_event"),
                             ["Berlin Wall construction 1961", "Berlin Wall checkpoint November 1989"])
        scene = scene_context(job, "Berlin Wall concrete watchtower")
        modern = score_media_candidate(scene, "Berlin Wall concrete watchtower",
                                       title="File:35th anniversary of the fall of the Berlin Wall event 2024.jpg")
        period = score_media_candidate(scene, "Berlin Wall concrete watchtower",
                                       title="File:Berlin Wall watchtower 1989.jpg")
        self.assertTrue(modern.accepted)  # soft
        self.assertGreater(period.score, modern.score)

    def test_era_penalty_not_applied_outside_historical_or_without_years(self):
        from app.media_relevance import scene_context
        job = build_media_topic_context("How Elevators Work", "educational_how_it_works")
        scene = scene_context(job, "elevator cabin doors")
        dated = score_media_candidate(scene, "elevator cabin doors", title="Elevator cabin doors 2024")
        undated = score_media_candidate(scene, "elevator cabin doors", title="Elevator cabin doors")
        self.assertEqual(dated.score, undated.score)
