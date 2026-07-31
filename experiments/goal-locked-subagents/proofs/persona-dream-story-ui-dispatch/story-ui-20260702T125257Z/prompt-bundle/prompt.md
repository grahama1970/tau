# RATIONALE (not sent to LLM)
# Purpose: Generate one grounded Phase 02 Embry/Kai story treatment JSON object from Phase 02 story inputs.
# Consumer: ux-lab /dream#story Author Console -> /api/tau/dream/story-draft -> Tau story-writer/story-editor loop.
# Why this matters: Bad output breaks storyboard generation by inventing assets, omitting environment physics, or producing prose that cannot populate the interaction matrix.
# Input: context.core_idea, context.location, context.environment, context.interaction_rows[], context.linked_assets[], author_profile
# Output: JSON object matching response_contract; consumed by Tau story agents and the Phase 02 Story Area.
# Last reviewed: 2026-07-01 by Graham/Codex

# Phase 02 Story Prompt Payload

Core idea: Embry and Kai both faked a sick day at their summer jobs to go surfing on the Big Island on a Wednesday in June of 2024 — Kona Coast, Kahaluʻu Bay, summer swell patterns, lava rock reefs, local surf etiquette.

Location: Kahaluʻu Bay, Kona Coast · Wednesday · daylight surf window · June · 2024

Environment: Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.

Author: Andy Weir

## Raw Prompt

## Role
You are the Phase 02 Story author for Embry OS.

## Task
Generate an original 1-panel, 10-second story beat for the Phase 02 Story pane. Return one JSON object that matches the Output Format section at the end of this prompt.

## Input Field Paths
- source_context.core_idea: story directive text.
- source_context.location: place, weekday, daylight/time window, month, and year.
- source_context.environment.description: weather, heat, humidity, swell, reef, light, water, fatigue, and physical constraints.
- source_context.environment.active_pressures[]: specific physical pressures the story must operationalize.
- source_context.interaction_rows[].id: stable row id that must be copied into interaction_matrix[].source_seed_id.
- source_context.interaction_rows[].category: one of character, character_object, environmental_force, location_social_system.
- source_context.interaction_rows[].objects[]: physical objects or body-worn items.
- source_context.interaction_rows[].dynamics: how the row behaves under the environment.
- source_context.interaction_rows[].note: script/panel staging instruction.
- source_context.linked_assets[].id: stable asset id that must be copied into asset_usage[].asset_id.
- source_context.linked_assets[].description: stored image, sound, video, or text description.
- source_context.author.memory_style_context: selected persona memory style that determines how the story is written.
- generation_directives.author_style_directive: high-level, non-imitative author craft traits.
- generation_directives.creativity_directive: slider value translated into concrete generation behavior.
- response_contract: strict JSON schema suitable for Pydantic/dataclass validation.

## Source Material
<source_context>
{
  "core_idea": "Embry and Kai both faked a sick day at their summer jobs to go surfing on the Big Island on a Wednesday in June of 2024 — Kona Coast, Kahaluʻu Bay, summer swell patterns, lava rock reefs, local surf etiquette.",
  "author": {
    "id": "andy_weir",
    "name": "Andy Weir",
    "memory_style_context": "The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it.",
    "expanded_style_guide": "Requested author reference: Andy Weir. Do not imitate this author directly. Translate the reference into high-level craft traits for an original Phase 02 story treatment. Stored persona style context: The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it. Use a competent, practical protagonist solving concrete physical problems under pressure. The problems should be real, specific, and visible in the scene. Solutions should be earned through observation, trial, failure, iteration, and clear causal reasoning. Technical detail must function as plot, not decoration. Exposition should feel like active problem-solving rather than lecturing. Every detail about swell timing, reef depth, softened wax, glare, heat, fatigue, phones, and etiquette should have consequences for character choices. Humor should come from intelligence, stress, and self-awareness, not from pasted-on jokes. Keep the tone conversational, optimistic, precise, propulsive, and human. Pacing should move through problem, constraint, attempted solution, complication, and embodied decision. The reader should understand the practical problem well enough to feel the satisfaction of the choice or solution. Avoid direct prose imitation, signature phrasing, borrowed character types, borrowed plots, or fan-fiction echoes. Use the craft traits only."
  },
  "location": {
    "place": "Kahaluʻu Bay",
    "region": "Kona Coast",
    "island": "Big Island",
    "weekday": "Wednesday",
    "month": "June",
    "year": 2024,
    "time_window": "daylight surf window",
    "display": "Kahaluʻu Bay, Kona Coast · Wednesday · daylight surf window · June · 2024"
  },
  "environment": {
    "id": "env-0",
    "description": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
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
    ]
  },
  "interaction_rows": [
    {
      "id": "seed-0",
      "name": "Embry",
      "category": "character",
      "objects": [
        "navy rashguard",
        "phone",
        "family obligations",
        "borrowed/older shortboard"
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Heat and humidity make her physically exposed: sweat, glare, and tired paddling turn autonomy into a bodily choice, not just an idea.",
      "note": "Script/panels should show sweat, squinting, salt on skin, careful hand placement, and fatigue in her paddle cadence before dialogue explains anything.",
      "is_complete": true
    },
    {
      "id": "seed-1",
      "name": "Kai",
      "category": "character",
      "objects": [
        "black rashguard",
        "phone call",
        "surf ritual",
        "familiar shortboard"
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Reads the swell while managing heat, glare, and patience; his competence shows in conserving effort instead of forcing the moment.",
      "note": "Stage Kai as physically adapted to the heat: calm breathing, economical paddling, shaded glances at the reef line, and small gestures that guide Embry without lecturing.",
      "is_complete": true
    },
    {
      "id": "seed-2",
      "name": "Embry surfboard",
      "category": "character_object",
      "objects": [
        "White shortboard",
        "performance shape",
        "visibly waxed deck",
        "likely older/borrowed",
        "rail pressure matters over shallow reef."
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Humidity and sun soften wax and make footing less certain; the board forces Embry to commit cleanly despite tired arms and slick contact points.",
      "note": "Panel details should include wax smears, sun glare on the deck, hands gripping rails, and foot placement uncertainty as the board reacts to chop and reef proximity.",
      "is_complete": true
    },
    {
      "id": "seed-3",
      "name": "Kai surfboard",
      "category": "character_object",
      "objects": [
        "White shortboard with darker underside/rail marks",
        "well-used and waxed",
        "familiar enough for quick reef-line decisions."
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "A waxed, familiar board lets Kai compensate for heat, chop, and glare; restraint is visible when he waits rather than wasting energy.",
      "note": "Use the board as proof of familiarity: worn rail marks, confident trim angle, efficient turns, and quick corrections under humid, high-glare conditions.",
      "is_complete": true
    },
    {
      "id": "seed-4",
      "name": "June Swell",
      "category": "environmental_force",
      "objects": [
        "sets",
        "tide window",
        "wave face"
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Creates the timing pressure that makes hesitation and trust visible.",
      "note": "Panels need repeating set rhythm: quiet water, approaching lump, glare on the face, then a fast decision point.",
      "is_complete": true
    },
    {
      "id": "seed-5",
      "name": "Lava Reef",
      "category": "environmental_force",
      "objects": [
        "sharp rock",
        "shallow line",
        "safe channel"
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Turns the environment into a hard boundary rather than background scenery.",
      "note": "Show the reef as a physical rule: dark shapes below clear water, shallow consequences, and characters adjusting line and timing around it.",
      "is_complete": true
    },
    {
      "id": "seed-6",
      "name": "Kona Coast",
      "category": "location_social_system",
      "objects": [
        "bay",
        "local etiquette",
        "reef break"
      ],
      "environment_ref": "env-0",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "dynamics": "Holds the scene inside a public place where local rules shape private choices.",
      "note": "Script beats should include public beach pressure, waiting turns, reading locals, and the contrast between private escape and shared water.",
      "is_complete": true
    }
  ],
  "linked_assets": [
    {
      "id": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "title": "Embry surfing on the Big Island",
      "description": "Embry, a young woman in a navy surf top, crouches low on a white surfboard carving across a small wave, one hand brushing the water as spray bursts around her. Golden light hits the Big Island coastline behind her, with palm trees, dark lava rocks, green mountains, and low clouds creating a dramatic tropical backdrop for Kai to spot her ride or call out from nearby.",
      "memory_key": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "media_type": "png",
      "source": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "visibility": "caption_grounded"
    },
    {
      "id": "embry_media_asset__assets_character_sheet_montage_jpg",
      "title": "Embry image media asset character sheet montage (assets). Asset path /mnt/storage12tb/media/personas/embry/assets/character sheet montage.jpg Relative path assets/character sheet montage.jpg Media",
      "description": "Embry is a young woman with brown hair tied back, expressive green-brown eyes, and a navy polo, shown in multiple reference poses: neutral, focused at a computer, explaining with her hand raised, tired in a hoodie, smiling outdoors, and working at a multi-monitor desk with notes, mugs, and office items. The settings alternate between bright office interiors and warm coastal balcony/beach views with palm trees, ocean, and golden sunlight, establishing her as a thoughtful tech-savvy character ready to enter a 10-second Embry/Kai surfing story.",
      "memory_key": "embry_media_asset__assets_character_sheet_montage_jpg",
      "media_type": "jpg",
      "source": "embry_media_asset__assets_character_sheet_montage_jpg",
      "visibility": "caption_grounded"
    },
    {
      "id": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "title": "Embry barrel wave surfing reference",
      "description": "Embry/Kai is a young surfer in a dark blue shirt crouched low on a white surfboard, riding inside a curling turquoise barrel wave with one hand skimming the water for balance. Warm golden light hits her focused face as spray arcs overhead, with tropical palms, green mountains, rocky shore, and partly cloudy sky visible beyond the wave.",
      "memory_key": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "media_type": "png",
      "source": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "visibility": "caption_grounded"
    },
    {
      "id": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "title": "Kai Akana surfing on the Big Island",
      "description": "Kai Akana, a young surfer in a black rash guard and board shorts, carves low on a small glassy wave as spray bursts around his white surfboard. Behind him, the Big Island coastline shows dark lava rocks, palm trees, and steep green mountains under warm golden light with low clouds, setting up a focused surfing moment for Embry/Kai.",
      "memory_key": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "media_type": "png",
      "source": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "visibility": "caption_grounded"
    },
    {
      "id": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "title": "Kai Akana character sheet",
      "description": "Kai Akana is a young Hawaiian/Norwegian/Japanese surfer with tan skin, curly dark hair, athletic build, and expressive brown eyes, shown in navy tees, a black wetsuit, and post-surf shirtless with a white surfboard. Use him on a bright tropical beach with blue ocean, green mountains, palms, and warm sun, actively carrying or steadying his board after a surf session while looking toward Embry with an easy, engaged smile.",
      "memory_key": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "media_type": "png",
      "source": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "visibility": "caption_grounded"
    }
  ]
}
</source_context>

## Generation Directives
<generation_directives>
{
  "thematic_pivot": "Autonomy vs. Obligation",
  "author_style_directive": {
    "requested_author": "Andy Weir",
    "style_policy": "High-level craft traits only; do not directly imitate the living author.",
    "memory_style_context": "The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it.",
    "expanded_style_guide": "Requested author reference: Andy Weir. Do not imitate this author directly. Translate the reference into high-level craft traits for an original Phase 02 story treatment. Stored persona style context: The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it. Use a competent, practical protagonist solving concrete physical problems under pressure. The problems should be real, specific, and visible in the scene. Solutions should be earned through observation, trial, failure, iteration, and clear causal reasoning. Technical detail must function as plot, not decoration. Exposition should feel like active problem-solving rather than lecturing. Every detail about swell timing, reef depth, softened wax, glare, heat, fatigue, phones, and etiquette should have consequences for character choices. Humor should come from intelligence, stress, and self-awareness, not from pasted-on jokes. Keep the tone conversational, optimistic, precise, propulsive, and human. Pacing should move through problem, constraint, attempted solution, complication, and embodied decision. The reader should understand the practical problem well enough to feel the satisfaction of the choice or solution. Avoid direct prose imitation, signature phrasing, borrowed character types, borrowed plots, or fan-fiction echoes. Use the craft traits only.",
    "style_summary": "Requested author reference: Andy Weir. Do not imitate this author directly. Translate the reference into high-level craft traits for an original Phase 02 story treatment. Stored persona style context: The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it. Use a competent, practical protagonist solving concrete physical problems under pressure. The problems should be real, specific, and visible in the scene. Solutions should be earned through observation, trial, failure, iteration, and clear causal reasoning. Technical detail must function as plot, not decoration. Exposition should feel like active problem-solving rather than lecturing. Every detail about swell timing, reef depth, softened wax, glare, heat, fatigue, phones, and etiquette should have consequences for character choices. Humor should come from intelligence, stress, and self-awareness, not from pasted-on jokes. Keep the tone conversational, optimistic, precise, propulsive, and human. Pacing should move through problem, constraint, attempted solution, complication, and embodied decision. The reader should understand the practical problem well enough to feel the satisfaction of the choice or solution. Avoid direct prose imitation, signature phrasing, borrowed character types, borrowed plots, or fan-fiction echoes. Use the craft traits only.",
    "actionable_traits": [
      "practical problem-solving under physical constraints",
      "clear cause-and-effect scene logic",
      "dry, understated observational humor",
      "technical specificity that changes character choices",
      "characters thinking through immediate problems step by step",
      "exposition that feels like active problem-solving rather than lecturing",
      "conversational, precise, propulsive pacing",
      "reader satisfaction from understanding the problem and the earned solution",
      "tension created by real-world timing, physics, etiquette, and limited information",
      "grounded stakes rather than melodrama"
    ],
    "application_to_this_story": [
      "Use swell timing as a procedural problem.",
      "Use the lava reef as a hard physical constraint.",
      "Use heat, humidity, softened wax, glare, and fatigue as active causes of mistakes or hesitation.",
      "Let Embry and Kai reveal character through how they solve or avoid problems in the water.",
      "Move through problem, constraint, attempted solution, complication, and embodied decision.",
      "Keep humor understated and observational, never jokey or detached from the stakes."
    ],
    "prohibited_imitation": [
      "Do not copy the requested author exact prose style.",
      "Do not echo specific phrasing, character types, plots, or scenes from the requested author works.",
      "Do not make the story sound like fan fiction of an existing book."
    ]
  },
  "creativity_directive": {
    "slider_value": 0.6,
    "label": "grounded moderate invention",
    "actionable_interpretation": "Stay realistic and physically plausible while allowing selective invented details that intensify tension, character contrast, and scene texture.",
    "allowed_inventions": [
      "small work-related phone interruptions",
      "specific family-obligation pressure for Embry",
      "a plausible local-etiquette tension in the lineup",
      "a softened-wax or grip problem caused by heat",
      "a tricky but realistic summer swell set",
      "small practical surf details that clarify risk and decision-making"
    ],
    "limits": [
      "no surrealism",
      "no supernatural events",
      "no catastrophic rescue sequence unless explicitly requested",
      "no major new plotline unrelated to the sick-day surf premise",
      "no exaggerated recklessness",
      "no melodramatic confession scene",
      "no ignoring the support matrix"
    ],
    "plot_risk_level": "moderate",
    "realism_requirement": "Every major beat must be explainable through character choice, surf conditions, reef constraints, social etiquette, heat, fatigue, or phone obligations."
  }
}
</generation_directives>

## Asset Policy
{
  "visibility": "caption_grounded_or_metadata_only",
  "rule": "Use stored media descriptions when present. If a linked asset lacks a description, use its title only and do not invent visual, audio, or video details from an inaccessible URL.",
  "allowed_asset_use": [
    "character identity continuity",
    "surfing pose and board continuity",
    "environment and coastline continuity",
    "sound or video reference only when a stored description exists"
  ],
  "forbidden_asset_use": [
    "do not infer facial features from a URL",
    "do not infer body type from a URL",
    "do not infer colors or clothing beyond prompt fields and stored descriptions",
    "do not claim to have seen media that is metadata-only"
  ]
}

## Constraints
- Use only facts present in source_context and generation_directives.
- Do not imitate any living author directly. Apply generation_directives.author_style_directive as high-level craft guidance only.
- The selected author determines prose behavior. Use source_context.author.memory_style_context and generation_directives.author_style_directive to shape rhythm, humor, technical detail, and causality.
- Apply generation_directives.creativity_directive. Creativity 0.6 means grounded moderate invention, not surrealism or melodrama.
- Treat the environment as plot machinery, not scenery.
- Produce exactly 1 panel(s) totaling 10 seconds, not a full short story and not an eight-beat treatment.
- Set panel_count to 1 and duration_seconds to 10.
- Return panels[] with exactly 1 item(s), and set panel equal to panels[0].
- Keep story to roughly 45-90 words so the panel sequence stays focused.
- Include one interaction_matrix row for every source_context.interaction_rows[] item where is_complete is true.
- The interaction_matrix is the completeness ledger: every character, object, location, environmental force, and relevant pressure used by the story must be explained there.
- Include asset_usage rows only for source_context.linked_assets[] entries that influence the story.
- Include top-level location and environment objects. They must be populated from source_context.location and source_context.environment, not omitted.
- Copy asset_usage[].asset_id from source_context.linked_assets[].id.
- Copy interaction_matrix[].source_seed_id from source_context.interaction_rows[].id.
- If Embry, Kai, a surfboard, reef, swell, phone, heat, humidity, glare, wax, or fatigue appears in source_context, show how it changes visible behavior.
- If a surfboard appears, mention shape, wax state, condition, or age in story or interaction_matrix.
- Show Kai competence through restraint and efficient movement, not lecturing.
- Show Embry autonomy through physical choices: hand placement, rail grip, paddle fatigue, uncertain footing, and commitment or withdrawal near reef.
- Keep dialogue sparse, practical, and character-revealing.
- Avoid generic surf cliches, melodrama, reckless danger, and savior dynamics.

## Invalid Output
- The response includes markdown, prose outside JSON, or a code fence.
- The response includes any top-level key not listed in response_contract.required.
- The response adds an asset_id that is not present in source_context.linked_assets[].id.
- The response omits any completed source_context.interaction_rows[].id from interaction_matrix[].source_seed_id.
- The story or panel ignores source_context.environment when describing character or object behavior.
- A surfboard appears but the output omits shape, wax state, condition, or age in story, panel, or interaction_matrix.
- The output expands into a multi-scene treatment instead of one 10-second panel beat.
- The output directly imitates a living author instead of using high-level craft traits.
- author_style_directive does not translate the requested author into high-level non-imitative craft traits.
- creativity_directive does not convert the slider value into concrete allowed inventions and limits.

## Complete Example
Example input:
{
  "context": {
    "core_idea": "Embry and Kai fake a sick day to surf at Kahaluʻu Bay.",
    "location": "Kahaluʻu Bay, Kona Coast · Wednesday · daylight surf window · June · 2024",
    "environment": "Hot humid air, bright glare, lava reef, and soft wax change footing and timing.",
    "interaction_rows": [
      {
        "id": "seed-embry",
        "name": "Embry",
        "category": "character",
        "objects": [
          "navy rashguard",
          "waxed older white shortboard",
          "phone"
        ],
        "environment_ref": "env-0",
        "dynamics": "Glare and fatigue make timing a physical test.",
        "note": "Show salt, sweat, careful rail grip, and hesitation before the wave.",
        "is_complete": true
      }
    ],
    "linked_assets": [
      {
        "id": "embry_media_asset__example_png",
        "title": "Embry surfing reference",
        "description": "Embry crouches on a white surfboard with lava rocks and green mountains behind her.",
        "memoryKey": "embry_media_asset__example_png",
        "mediaType": "image",
        "visibility": "caption_grounded"
      }
    ]
  }
}

Expected output:
{
  "story": "Embry’s phone buzzes inside the beach bag just as a clean shoulder stands up over the reef; she squints through the glare, palms slipping on sun-soft wax, and chooses the paddle while Kai, already angled safely outside, only lifts two fingers toward the channel instead of telling her what to do.",
  "panel_count": 1,
  "duration_seconds": 10,
  "location": {
    "place": "Kahaluʻu Bay, Kona Coast, Big Island",
    "time": "Wednesday daylight surf window",
    "month": "June",
    "year": 2024,
    "description": "A public Kona Coast surf break where private escape is constrained by shared lineup rules."
  },
  "environment": {
    "weather_description": "Hot, humid coastal air with bright glare, saltwater, summer swell, shallow lava reef, and sun-softened wax.",
    "active_pressures": [
      "heat",
      "humidity",
      "glare",
      "softened wax",
      "fatigue",
      "lava reef caution",
      "local etiquette"
    ],
    "story_effect": "The weather and reef make each surf decision physical: grip, timing, patience, and restraint all matter."
  },
  "panel": {
    "shot": "Low waterline three-quarter shot facing the reef line, with Embry in the foreground on the older white shortboard and Kai farther out, half-turned toward the incoming set.",
    "action": "A June swell rises over the dark lava shapes; Embry commits to the paddle despite sweat, glare, and the phone buzzing onshore.",
    "emotional_turn": "Embry moves from borrowed escape to embodied choice: she is still obligated, still exposed, but the decision is hers.",
    "dialogue": null
  },
  "panels": [
    {
      "shot": "Low waterline three-quarter shot facing the reef line, with Embry in the foreground on the older white shortboard and Kai farther out, half-turned toward the incoming set.",
      "action": "A June swell rises over the dark lava shapes; Embry commits to the paddle despite sweat, glare, and the phone buzzing onshore.",
      "emotional_turn": "Embry moves from borrowed escape to embodied choice: she is still obligated, still exposed, but the decision is hers.",
      "dialogue": null
    }
  ],
  "interaction_matrix": [
    {
      "source_seed_id": "seed-embry",
      "entity": "Embry",
      "category": "character",
      "objects_used": [
        "navy rashguard",
        "waxed older white shortboard",
        "phone"
      ],
      "environment_interaction": "Humidity softens wax, glare hides the reef line, and fatigue makes her commitment visible.",
      "story_function": "Turns autonomy into a bodily choice in the exact surf moment.",
      "visible_in_panel": true
    }
  ],
  "asset_usage": [
    {
      "asset_id": "embry_media_asset__example_png",
      "used_for": "Embry body posture, surfboard color, lava rock coastline, and mountain backdrop.",
      "usage_confidence": "caption_grounded"
    }
  ],
  "style_application": {
    "author_reference_used_as": "High-level craft guidance: practical cause-and-effect staging, physical constraints, and dry restraint without direct imitation.",
    "creativity_level_used_as": "Grounded moderate invention: a plausible phone buzz and decisive swell heighten the moment without breaking realism."
  },
  "quality_checks": {
    "covered_seed_ids": [
      "seed-embry"
    ],
    "missing_seed_ids": [],
    "used_only_provided_context": true,
    "no_direct_author_imitation": true,
    "valid_one_panel_10_second_moment": true
  }
}

## Output Format
Output NOTHING but one raw JSON object. No markdown fence, heading, preamble, explanation, or trailing notes.
Start with { and end with }.
Return this exact JSON schema:
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "story",
    "panel_count",
    "duration_seconds",
    "location",
    "environment",
    "panel",
    "panels",
    "interaction_matrix",
    "asset_usage",
    "style_application",
    "quality_checks"
  ],
  "properties": {
    "story": {
      "type": "string",
      "minLength": 180,
      "maxLength": 810,
      "description": "A concise, human-written story beat for 1 panel(s) and 10 seconds, approximately 45-90 words."
    },
    "panel_count": {
      "type": "number",
      "const": 1,
      "description": "The exact number of story panels requested by the Phase 02 controls."
    },
    "duration_seconds": {
      "type": "number",
      "const": 10,
      "description": "Target duration represented by the requested panel sequence."
    },
    "location": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "place",
        "time",
        "month",
        "year",
        "description"
      ],
      "properties": {
        "place": {
          "type": "string",
          "description": "Place name and region from source_context.location."
        },
        "time": {
          "type": "string",
          "description": "Weekday and daylight/time window from source_context.location."
        },
        "month": {
          "type": "string",
          "description": "Month from source_context.location."
        },
        "year": {
          "type": "number",
          "description": "Year from source_context.location."
        },
        "description": {
          "type": "string",
          "description": "Concise setting description used by the story."
        }
      }
    },
    "environment": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "weather_description",
        "active_pressures",
        "story_effect"
      ],
      "properties": {
        "weather_description": {
          "type": "string",
          "description": "Descriptive weather and surf conditions characters physically respond to."
        },
        "active_pressures": {
          "type": "array",
          "minItems": 4,
          "items": {
            "type": "string"
          }
        },
        "story_effect": {
          "type": "string",
          "description": "How weather, surf, reef, and public beach pressure drive the story beat."
        }
      }
    },
    "panel": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "shot",
        "action",
        "emotional_turn",
        "dialogue"
      ],
      "properties": {
        "shot": {
          "type": "string",
          "description": "Camera/framing for this panel."
        },
        "action": {
          "type": "string",
          "description": "What happens in this panel moment."
        },
        "emotional_turn": {
          "type": "string",
          "description": "The visible internal shift."
        },
        "dialogue": {
          "type": [
            "string",
            "null"
          ],
          "description": "One short line or null."
        }
      },
      "description": "Primary or first panel, duplicated from panels[0] for consumers that expect a single panel."
    },
    "panels": {
      "type": "array",
      "minItems": 1,
      "maxItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "shot",
          "action",
          "emotional_turn",
          "dialogue"
        ],
        "properties": {
          "shot": {
            "type": "string",
            "description": "Camera/framing for this panel."
          },
          "action": {
            "type": "string",
            "description": "What happens in this panel moment."
          },
          "emotional_turn": {
            "type": "string",
            "description": "The visible internal shift."
          },
          "dialogue": {
            "type": [
              "string",
              "null"
            ],
            "description": "One short line or null."
          }
        }
      },
      "description": "Exactly panel_count panels. For one panel, this array contains the same panel as panel."
    },
    "interaction_matrix": {
      "type": "array",
      "minItems": 7,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "source_seed_id",
          "entity",
          "category",
          "objects_used",
          "environment_interaction",
          "story_function",
          "visible_in_panel"
        ],
        "properties": {
          "source_seed_id": {
            "type": "string",
            "description": "Copy from source_context.interaction_rows[].id."
          },
          "entity": {
            "type": "string",
            "description": "Copy from source_context.interaction_rows[].name."
          },
          "category": {
            "type": "string",
            "enum": [
              "character",
              "character_object",
              "environmental_force",
              "location_social_system"
            ]
          },
          "objects_used": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "environment_interaction": {
            "type": "string",
            "description": "Complete explanation of how heat, humidity, water, reef, light, fatigue, or public etiquette changes this entity/object/force."
          },
          "story_function": {
            "type": "string",
            "description": "Why this row matters to the one-panel story beat and what would be missing if it were removed."
          },
          "visible_in_panel": {
            "type": "boolean"
          }
        }
      }
    },
    "asset_usage": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "asset_id",
          "used_for",
          "usage_confidence"
        ],
        "properties": {
          "asset_id": {
            "type": "string",
            "description": "Copy from source_context.linked_assets[].id."
          },
          "used_for": {
            "type": "string",
            "description": "Specific visual, audio, video, or text grounding role in the story."
          },
          "usage_confidence": {
            "type": "string",
            "enum": [
              "metadata_only",
              "caption_grounded",
              "image_grounded",
              "audio_grounded",
              "video_grounded"
            ]
          }
        }
      }
    },
    "style_application": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "author_reference_used_as",
        "creativity_level_used_as"
      ],
      "properties": {
        "author_reference_used_as": {
          "type": "string"
        },
        "creativity_level_used_as": {
          "type": "string"
        }
      }
    },
    "quality_checks": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "covered_seed_ids",
        "missing_seed_ids",
        "used_only_provided_context",
        "no_direct_author_imitation",
        "valid_one_panel_10_second_moment"
      ],
      "properties": {
        "covered_seed_ids": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "missing_seed_ids": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "used_only_provided_context": {
          "type": "boolean"
        },
        "no_direct_author_imitation": {
          "type": "boolean"
        },
        "valid_one_panel_10_second_moment": {
          "type": "boolean"
        }
      }
    }
  }
}
