# Story Contract

Status: `ACCEPTED_AUTOMATED`

Target duration: `5.0` seconds

Speaking characters: Embry

## Story

{
  "story": "Embry’s phone buzzes on the beach bag like obligation had learned echolocation. Ten yards out, a clean June lump lifts over the dark lava reef; glare turns the face silver, and her older white shortboard’s softened wax lets one palm skid. Kai, outside on his familiar board, doesn’t call her in. He waits, trims aside for a local, then points two fingers at the safe channel. Embry resets her rail grip, saves three tired strokes, and chooses the wave.",
  "panel_count": 1,
  "duration_seconds": 10,
  "location": {
    "place": "Kahaluʻu Bay, Kona Coast, Big Island",
    "time": "Wednesday daylight surf window",
    "month": "June",
    "year": 2024,
    "description": "A public Kona Coast reef break where a stolen sick-day surf session is constrained by local etiquette, shared water, and shallow lava rock."
  },
  "environment": {
    "weather_description": "Hot, humid coastal air with summer swell patterns, harsh glare, saltwater, softened wax, fatigue, and clear water over shallow lava reef.",
    "active_pressures": [
      "sweat",
      "glare",
      "wax softness",
      "saltwater",
      "fatigue",
      "grip changes",
      "footing changes",
      "board control changes",
      "reef caution",
      "social patience"
    ],
    "story_effect": "Heat, glare, fatigue, and softened wax turn the incoming set into a practical timing problem, while the reef and local lineup rules force Embry and Kai to choose restraint before commitment."
  },
  "panel": {
    "shot": "Low waterline three-quarter shot across Kahaluʻu Bay: Embry foreground on an older white shortboard, navy rashguard wet with salt and sweat, Kai farther outside in black, dark reef shapes visible beneath the clear water.",
    "action": "A silver-glared swell stands up over the lava reef as Embry’s phone buzzes onshore; her palm slips on sun-soft wax, Kai yields to a local and points at the safe channel, and Embry regrips the rail before committing to three efficient strokes.",
    "emotional_turn": "Embry turns a fake sick day from evasion into autonomy by making the safest, smartest physical choice herself.",
    "dialogue": "Kai: “Channel, then go.”"
  },
  "panels": [
    {
      "shot": "Low waterline three-quarter shot across Kahaluʻu Bay: Embry foreground on an older white shortboard, navy rashguard wet with salt and sweat, Kai farther outside in black, dark reef shapes visible beneath the clear water.",
      "action": "A silver-glared swell stands up over the lava reef as Embry’s phone buzzes onshore; her palm slips on sun-soft wax, Kai yields to a local and points at the safe channel, and Embry regrips the rail before committing to three efficient strokes.",
      "emotional_turn": "Embry turns a fake sick day from evasion into autonomy by making the safest, smartest physical choice herself.",
      "dialogue": "Kai: “Channel, then go.”"
    }
  ],
  "interaction_matrix": [
    {
      "source_seed_id": "seed-0",
      "entity": "Embry",
      "category": "character",
      "objects_used": [
        "navy rashguard",
        "phone",
        "family obligations",
        "borrowed/older shortboard"
      ],
      "environment_interaction": "Heat and humidity put sweat and salt on her skin, glare narrows her sightline to the wave face, fatigue limits her paddle strokes, and the reef makes her rail grip and timing matter.",
      "story_function": "Embry carries the autonomy-versus-obligation pivot by ignoring the buzzing phone long enough to make a careful, competent surf decision.",
      "visible_in_panel": true,
      "description": "Embry: Heat and humidity make her physically exposed: sweat, glare, and tired paddling turn autonomy into a bodily choice, not just an idea. Script/panels should show sweat, squinting, salt on skin, careful hand placement, and fatigue in her paddle cadence before dialogue explains anything."
    },
    {
      "source_seed_id": "seed-1",
      "entity": "Kai",
      "category": "character",
      "objects_used": [
        "black rashguard",
        "phone call",
        "surf ritual",
        "familiar shortboard"
      ],
      "environment_interaction": "Kai reads the glare, swell angle, reef line, and lineup pressure without wasting motion; heat and fatigue make his restraint more useful than a lecture.",
      "story_function": "Kai demonstrates competence by yielding to a local, conserving energy, and giving one practical channel cue rather than taking over Embry’s choice.",
      "visible_in_panel": true,
      "description": "Kai: Reads the swell while managing heat, glare, and patience; his competence shows in conserving effort instead of forcing the moment. Stage Kai as physically adapted to the heat: calm breathing, economical paddling, shaded glances at the reef line, and small gestures that guide Embry without lecturing."
    },
    {
      "source_seed_id": "seed-2",
      "entity": "Embry surfboard",
      "category": "character_object",
      "objects_used": [
        "White shortboard",
        "performance shape",
        "visibly waxed deck",
        "likely older/borrowed",
        "rail pressure matters over shallow reef."
      ],
      "environment_interaction": "Sun and humidity soften the wax on the older white performance shortboard, so Embry’s palm slips and her rail pressure must be reset before the wave reaches the reef.",
      "story_function": "The board converts heat and reef caution into visible mechanics: grip, balance, and commitment are not abstract feelings.",
      "visible_in_panel": true,
      "description": "Embry surfboard: Humidity and sun soften wax and make footing less certain; the board forces Embry to commit cleanly despite tired arms and slick contact points. Panel details should include wax smears, sun glare on the deck, hands gripping rails, and foot placement uncertainty as the board reacts to chop and reef proximity."
    },
    {
      "source_seed_id": "seed-3",
      "entity": "Kai surfboard",
      "category": "character_object",
      "objects_used": [
        "White shortboard with darker underside/rail marks",
        "well-used and waxed",
        "familiar enough for quick reef-line decisions."
      ],
      "environment_interaction": "The well-used waxed board lets Kai trim efficiently under glare and chop, making small corrections while he waits outside the danger line.",
      "story_function": "Kai’s familiar board makes his restraint legible as skill: he can move quickly, but chooses patience and positioning.",
      "visible_in_panel": true,
      "description": "Kai surfboard: A waxed, familiar board lets Kai compensate for heat, chop, and glare; restraint is visible when he waits rather than wasting energy. Use the board as proof of familiarity: worn rail marks, confident trim angle, efficient turns, and quick corrections under humid, high-glare conditions."
    },
    {
      "source_seed_id": "seed-4",
      "entity": "June Swell",
      "category": "environmental_force",
      "objects_used": [
        "sets",
        "tide window",
        "wave face"
      ],
      "environment_interaction": "The summer set rhythm creates a brief decision window: quiet water becomes a silver-glared wave face, forcing Embry to decide before fatigue costs her the takeoff.",
      "story_function": "The swell supplies the ten-second clock and turns hesitation, etiquette, and trust into immediate action.",
      "visible_in_panel": true,
      "description": "June Swell: Creates the timing pressure that makes hesitation and trust visible. Panels need repeating set rhythm: quiet water, approaching lump, glare on the face, then a fast decision point."
    },
    {
      "source_seed_id": "seed-5",
      "entity": "Lava Reef",
      "category": "environmental_force",
      "objects_used": [
        "sharp rock",
        "shallow line",
        "safe channel"
      ],
      "environment_interaction": "Dark lava shapes under clear saltwater mark a shallow hard boundary; Embry and Kai adjust line, paddle timing, and channel choice around it.",
      "story_function": "The reef prevents generic surf action by making every movement accountable to a visible physical consequence.",
      "visible_in_panel": true,
      "description": "Lava Reef: Turns the environment into a hard boundary rather than background scenery. Show the reef as a physical rule: dark shapes below clear water, shallow consequences, and characters adjusting line and timing around it."
    },
    {
      "source_seed_id": "seed-6",
      "entity": "Kona Coast",
      "category": "location_social_system",
      "objects_used": [
        "bay",
        "local etiquette",
        "reef break"
      ],
      "environment_interaction": "The public bay and reef break require waiting turns and reading locals; heat and crowded patience make Kai’s yield and Embry’s timing socially as well as physically necessary.",
      "story_function": "The location makes their private sick-day escape depend on shared-water rules, preventing reckless or isolated decision-making.",
      "visible_in_panel": true,
      "description": "Kona Coast: Holds the scene inside a public place where local rules shape private choices. Script beats should include public beach pressure, waiting turns, reading locals, and the contrast between private escape and shared water."
    }
  ],
  "asset_usage": [
    {
      "asset_id": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "used_for": "Embry’s navy surf top, low crouched surf posture, white surfboard continuity, spray, lava rocks, coastline, palms, green mountains, and warm Big Island light.",
      "usage_confidence": "caption_grounded",
      "title": "Embry surfing on the Big Island",
      "use_in_story": "Embry’s navy surf top, low crouched surf posture, white surfboard continuity, spray, lava rocks, coastline, palms, green mountains, and warm Big Island light.",
      "visual_consistency_note": "Embry, a young woman in a navy surf top, crouches low on a white surfboard carving across a small wave, one hand brushing the water as spray bursts around her. Golden light hits the Big Island coastline behind her, with palm trees, dark lava rocks, green mountains, and low clouds creating a dramatic tropical backdrop for Kai to spot her ride or call out from nearby."
    },
    {
      "asset_id": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "used_for": "Kai’s black rashguard, white surfboard, low competent carving posture, spray, and Big Island lava-rock coastal context.",
      "usage_confidence": "caption_grounded",
      "title": "Kai Akana surfing on the Big Island",
      "use_in_story": "Kai’s black rashguard, white surfboard, low competent carving posture, spray, and Big Island lava-rock coastal context.",
      "visual_consistency_note": "Kai Akana, a young surfer in a black rash guard and board shorts, carves low on a small glassy wave as spray bursts around his white surfboard. Behind him, the Big Island coastline shows dark lava rocks, palm trees, and steep green mountains under warm golden light with low clouds, setting up a focused surfing moment for Embry/Kai."
    }
  ],
  "style_application": {
    "author_reference_used_as": "High-level non-imitative craft guidance: the beat uses concrete surf physics, cause-and-effect constraints, practical decision-making, and understated pressure-based humor without copying prose or plot.",
    "creativity_level_used_as": "Grounded moderate invention: the buzzing phone and exact lineup cue intensify the sick-day obligation and local-etiquette tension while staying realistic and physically plausible."
  },
  "quality_checks": {
    "covered_seed_ids": [
      "seed-0",
      "seed-1",
      "seed-2",
      "seed-3",
      "seed-4",
      "seed-5",
      "seed-6"
    ],
    "missing_seed_ids": [],
    "used_only_provided_context": true,
    "no_direct_author_imitation": true,
    "valid_one_panel_10_second_moment": true
  }
}
