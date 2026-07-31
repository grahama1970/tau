# Story Contract

Status: `ACCEPTED_AUTOMATED`

Target duration: `5.0` seconds

Speaking characters: Embry

## Story

## Role
You are the Phase 02 Story author for Embry OS.

## Task
Generate one Phase 02 story treatment for an original surf-centered fiction scene. Return one JSON object that matches the Output Format section at the end of this prompt.

## Input Field Paths
- context.core_idea: story directive text.
- context.location: place, weekday, daylight/time window, month, and year.
- context.environment: weather, heat, humidity, swell, reef, light, water, fatigue, and physical constraints.
- context.interaction_rows[].id: stable row id that must be copied into interaction_matrix[].source_row_id.
- context.interaction_rows[].name: character, object, place, or environmental force.
- context.interaction_rows[].objects: physical objects or body-worn items.
- context.interaction_rows[].dynamics: how the row behaves under the environment.
- context.interaction_rows[].note: script/panel staging instruction.
- context.linked_assets[].id: stable asset id that must be copied into asset_usage[].asset_id.
- context.linked_assets[].memoryKey: persona_memory key for the asset.
- context.linked_assets[].description: stored image, sound, video, or text description.
- author_profile.persona and author_profile.persona_context: writing style constraints.
- response_contract: strict JSON schema suitable for Pydantic/dataclass validation.

## Source Material
<context>
{
  "core_idea": "Embry and Kai both faked a sick day at their summer jobs to go surfing on the Big Island on a Wednesday in June of 2024 — Kona Coast, Kahaluʻu Bay, summer swell patterns, lava rock reefs, local surf etiquette.",
  "location": "Kahaluʻu Bay, Kona Coast · Wednesday · daylight surf window · June · 2024",
  "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
  "interaction_rows": [
    {
      "name": "Embry",
      "objects": "navy rashguard, phone, family obligations, borrowed/older shortboard",
      "dynamics": "Heat and humidity make her physically exposed: sweat, glare, and tired paddling turn autonomy into a bodily choice, not just an idea.",
      "note": "Script/panels should show sweat, squinting, salt on skin, careful hand placement, and fatigue in her paddle cadence before dialogue explains anything.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-0",
      "isComplete": true
    },
    {
      "name": "Kai",
      "objects": "black rashguard, phone call, surf ritual, familiar shortboard",
      "dynamics": "Reads the swell while managing heat, glare, and patience; his competence shows in conserving effort instead of forcing the moment.",
      "note": "Stage Kai as physically adapted to the heat: calm breathing, economical paddling, shaded glances at the reef line, and small gestures that guide Embry without lecturing.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-1",
      "isComplete": true
    },
    {
      "name": "Embry surfboard",
      "objects": "White shortboard, performance shape, visibly waxed deck, likely older/borrowed, rail pressure matters over shallow reef.",
      "dynamics": "Humidity and sun soften wax and make footing less certain; the board forces Embry to commit cleanly despite tired arms and slick contact points.",
      "note": "Panel details should include wax smears, sun glare on the deck, hands gripping rails, and foot placement uncertainty as the board reacts to chop and reef proximity.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-2",
      "isComplete": true
    },
    {
      "name": "Kai surfboard",
      "objects": "White shortboard with darker underside/rail marks, well-used and waxed, familiar enough for quick reef-line decisions.",
      "dynamics": "A waxed, familiar board lets Kai compensate for heat, chop, and glare; restraint is visible when he waits rather than wasting energy.",
      "note": "Use the board as proof of familiarity: worn rail marks, confident trim angle, efficient turns, and quick corrections under humid, high-glare conditions.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-3",
      "isComplete": true
    },
    {
      "name": "June Swell",
      "objects": "sets, tide window, wave face",
      "dynamics": "Creates the timing pressure that makes hesitation and trust visible.",
      "note": "Panels need repeating set rhythm: quiet water, approaching lump, glare on the face, then a fast decision point.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-4",
      "isComplete": true
    },
    {
      "name": "Lava Reef",
      "objects": "sharp rock, shallow line, safe channel",
      "dynamics": "Turns the environment into a hard boundary rather than background scenery.",
      "note": "Show the reef as a physical rule: dark shapes below clear water, shallow consequences, and characters adjusting line and timing around it.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-5",
      "isComplete": true
    },
    {
      "name": "Kona Coast",
      "objects": "bay, local etiquette, reef break",
      "dynamics": "Holds the scene inside a public place where local rules shape private choices.",
      "note": "Script beats should include public beach pressure, waiting turns, reading locals, and the contrast between private escape and shared water.",
      "environment": "Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.",
      "id": "seed-6",
      "isComplete": true
    }
  ],
  "linked_assets": [
    {
      "id": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "title": "Embry surfing on the Big Island",
      "url": "/api/persona-media?persona=embry&path=assets%2Fsurfing%2Fembry_surfing_big_island_2024.png",
      "description": "Embry, a young woman in a navy surf top, crouches low on a white surfboard carving across a small wave, one hand brushing the water as spray bursts around her. Golden light hits the Big Island coastline behind her, with palm trees, dark lava rocks, green mountains, and low clouds creating a dramatic tropical backdrop for Kai to spot her ride or call out from nearby.",
      "source": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "memoryKey": "embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png",
      "mediaType": "png"
    },
    {
      "id": "embry_media_asset__assets_character_sheet_montage_jpg",
      "title": "Embry image media asset character sheet montage (assets). Asset path /mnt/storage12tb/media/personas/embry/assets/character sheet montage.jpg Relative path assets/character sheet montage.jpg Media",
      "url": "/api/persona-media?persona=embry&path=assets%2Fcharacter_sheet_montage.jpg",
      "description": "Embry is a young woman with brown hair tied back, expressive green-brown eyes, and a navy polo, shown in multiple reference poses: neutral, focused at a computer, explaining with her hand raised, tired in a hoodie, smiling outdoors, and working at a multi-monitor desk with notes, mugs, and office items. The settings alternate between bright office interiors and warm coastal balcony/beach views with palm trees, ocean, and golden sunlight, establishing her as a thoughtful tech-savvy character ready to enter a 10-second Embry/Kai surfing story.",
      "source": "embry_media_asset__assets_character_sheet_montage_jpg",
      "memoryKey": "embry_media_asset__assets_character_sheet_montage_jpg",
      "mediaType": "jpg"
    },
    {
      "id": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "title": "Embry barrel wave surfing reference",
      "url": "/api/persona-media?persona=embry&path=assets%2Fsurfing%2Fembry_barrel_wave_big_island_2024.png",
      "description": "Embry/Kai is a young surfer in a dark blue shirt crouched low on a white surfboard, riding inside a curling turquoise barrel wave with one hand skimming the water for balance. Warm golden light hits her focused face as spray arcs overhead, with tropical palms, green mountains, rocky shore, and partly cloudy sky visible beyond the wave.",
      "source": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "memoryKey": "embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png",
      "mediaType": "png"
    },
    {
      "id": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "title": "Kai Akana surfing on the Big Island",
      "url": "/api/persona-media?persona=kai_akana&path=assets%2Fsurfing%2Fkai_surfing_big_island_2024.png",
      "description": "Kai Akana, a young surfer in a black rash guard and board shorts, carves low on a small glassy wave as spray bursts around his white surfboard. Behind him, the Big Island coastline shows dark lava rocks, palm trees, and steep green mountains under warm golden light with low clouds, setting up a focused surfing moment for Embry/Kai.",
      "source": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "memoryKey": "kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png",
      "mediaType": "png"
    },
    {
      "id": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "title": "Kai Akana character sheet",
      "url": "/api/persona-media?persona=kai_akana&path=assets%2Fcontact_sheets%2Fkai_akana_character_sheet.png",
      "description": "Kai Akana is a young Hawaiian/Norwegian/Japanese surfer with tan skin, curly dark hair, athletic build, and expressive brown eyes, shown in navy tees, a black wetsuit, and post-surf shirtless with a white surfboard. Use him on a bright tropical beach with blue ocean, green mountains, palms, and warm sun, actively carrying or steadying his board after a surf session while looking toward Embry with an easy, engaged smile.",
      "source": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "memoryKey": "kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png",
      "mediaType": "png"
    }
  ]
}
</context>

<author_profile>
{
  "persona_id": "andy_weir",
  "persona": "Andy Weir",
  "persona_context": "The protagonist is competent. The problems are real. The solutions are earned. Science is not a backdrop—it's the plot. Humor comes from intelligence under pressure. Every technical detail matters. If you can't explain it, you can't write it.",
  "creativity_index": 0.6
}
</author_profile>

## Author Style Directive
{
  "requested_author": "Andy Weir",
  "style_policy": "High-level craft traits only; do not directly imitate the living author.",
  "style_summary": "Original prose using practical problem-solving energy, dry observational humor, clear cause-and-effect logic, and grounded technical detail.",
  "actionable_traits": [
    "practical problem-solving under physical constraints",
    "clear cause-and-effect scene logic",
    "dry, understated observational humor",
    "technical specificity that changes character choices",
    "characters thinking through immediate problems step by step",
    "tension created by real-world timing, physics, etiquette, and limited information",
    "grounded stakes rather than melodrama"
  ],
  "application_to_this_story": [
    "Use swell timing as a procedural problem.",
    "Use the lava reef as a hard physical constraint.",
    "Use heat, humidity, softened wax, glare, and fatigue as active causes of mistakes or hesitation.",
    "Let Embry and Kai reveal character through how they solve or avoid problems in the water.",
    "Keep humor understated and observational, never jokey or detached from the stakes."
  ],
  "prohibited_imitation": [
    "Do not copy the requested author exact prose style.",
    "Do not echo specific phrasing, character types, plots, or scenes from the requested author works.",
    "Do not make the story sound like fan fiction of an existing book."
  ]
}

## Creativity Directive
{
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

## Constraints
- Use only facts present in context and author_profile.
- Use an original voice with practical problem-solving energy, dry observational humor, clear cause-and-effect logic, and grounded technical detail.
- Do not imitate any living author directly. Abstract the selected Author into constraints; do not copy diction, cadence, or signature style.
- Treat the environment as plot machinery, not scenery.
- Include exactly eight story_treatment rows with the required beat_id values.
- Include one interaction_matrix row for every context.interaction_rows[] item.
- Include asset_usage rows only for context.linked_assets[] entries that influence the story.
- Copy asset_usage[].asset_id from context.linked_assets[].id.
- Copy interaction_matrix[].source_row_id from context.interaction_rows[].id.
- If Embry, Kai, a surfboard, reef, swell, phone, heat, humidity, glare, wax, or fatigue appears in context, show how it changes visible behavior.
- If a surfboard appears, mention shape, wax state, condition, or age in story or interaction_matrix.
- Show Kai competence through restraint and efficient movement, not lecturing.
- Show Embry autonomy through physical choices: hand placement, rail grip, paddle fatigue, uncertain footing, and commitment or withdrawal near reef.
- Keep dialogue sparse, practical, and character-revealing.
- Avoid generic surf cliches, melodrama, reckless danger, and savior dynamics.

## Invalid Output
- The response includes markdown, prose outside JSON, or a code fence.
- The response includes any top-level key not listed in response_contract.required.
- The response adds an asset_id that is not present in context.linked_assets[].id.
- The response omits any context.interaction_rows[].id from interaction_matrix[].source_row_id.
- The story treatment ignores context.environment when describing character or object behavior.
- A surfboard appears but the output omits shape, wax state, condition, or age in story_treatment or interaction_matrix.
- story_treatment does not contain exactly the eight required beat_id values.
- The output directly imitates a living author instead of using the abstract author_profile constraints.
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
        "objects": "navy rashguard, waxed older white shortboard, phone",
        "environment": "Hot humid air and shallow lava reef.",
        "dynamics": "Glare and fatigue make timing a physical test.",
        "note": "Show salt, sweat, careful rail grip, and hesitation before the wave.",
        "isComplete": true
      }
    ],
    "linked_assets": [
      {
        "id": "embry_media_asset__example_png",
        "title": "Embry surfing reference",
        "description": "Embry crouches on a white surfboard with lava rocks and green mountains behind her.",
        "memoryKey": "embry_media_asset__example_png",
        "mediaType": "image"
      }
    ]
  }
}

Expected output:
{
  "title": "The Reef Line",
  "logline": "A stolen sick day at Kahaluʻu Bay forces Embry and Kai to test whether freedom can survive heat, reef, etiquette, and the phones waiting onshore.",
  "tone": "Grounded, kinetic, technically observant, and restrained.",
  "author_style_directive": {
    "requested_author": "Andy Weir",
    "style_policy": "High-level craft traits only; do not directly imitate the living author.",
    "style_summary": "Original prose using practical problem-solving energy, dry observational humor, clear cause-and-effect logic, and grounded technical detail.",
    "actionable_traits": [
      "practical problem-solving under physical constraints",
      "clear cause-and-effect scene logic",
      "dry, understated observational humor",
      "technical specificity that changes character choices",
      "characters thinking through immediate problems step by step",
      "tension created by real-world timing, physics, etiquette, and limited information",
      "grounded stakes rather than melodrama"
    ],
    "application_to_this_story": [
      "Use swell timing as a procedural problem.",
      "Use the lava reef as a hard physical constraint.",
      "Use heat, humidity, softened wax, glare, and fatigue as active causes of mistakes or hesitation.",
      "Let Embry and Kai reveal character through how they solve or avoid problems in the water.",
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
  },
  "main_dramatic_question": "Can Embry claim one clean moment of agency without turning Kai into a rescuer or the reef into a dare?",
  "character_arcs": [
    {
      "character": "Embry",
      "starting_pressure": "Family obligations and the sick-day lie keep pulling her attention back to shore.",
      "turn": "She treats the board, wax, glare, and reef as real constraints instead of symbols.",
      "ending_state": "She makes a deliberate physical choice in the water."
    },
    {
      "character": "Kai",
      "starting_pressure": "He knows the break and could dominate the moment.",
      "turn": "He chooses restraint and lets his competence appear through timing.",
      "ending_state": "He supports Embry without managing her."
    }
  ],
  "story_treatment": [
    {
      "beat_id": "opening_image",
      "heading": "Arrival",
      "treatment": "Embry and Kai converge at the hot public beach with phones muted and boards warming under the glare."
    },
    {
      "beat_id": "the_lie",
      "heading": "Phones",
      "treatment": "Notifications keep flashing from bags onshore, making the sick day feel temporary and expensive."
    },
    {
      "beat_id": "entering_the_water",
      "heading": "Lineup",
      "treatment": "Kai reads the local order by waiting; Embry studies his restraint while gripping the wax-softened rail."
    },
    {
      "beat_id": "failed_or_hesitant_attempt",
      "heading": "Hesitation",
      "treatment": "Embry checks the dark reef shape too late and loses a set to glare, fatigue, and self-protection."
    },
    {
      "beat_id": "kai_restraint",
      "heading": "Kai Waits",
      "treatment": "Kai could take the next clean wave but sits back, breathing evenly and tracking the safe channel."
    },
    {
      "beat_id": "mid_scene_tension",
      "heading": "Question",
      "treatment": "A short exchange makes the sick-day lie sharper without explaining it away."
    },
    {
      "beat_id": "decisive_set",
      "heading": "Commitment",
      "treatment": "A clean summer lump arrives and Embry must choose from her body: paddle, angle, hands, feet, rail."
    },
    {
      "beat_id": "resolution",
      "heading": "Temporary Freedom",
      "treatment": "The ending leaves the phones waiting while the water briefly proves the day was real."
    }
  ],
  "interaction_matrix": [
    {
      "entity": "Embry",
      "objects": "navy rashguard, waxed older white shortboard, phone",
      "environment_interaction": "Humidity softens wax, glare hides the reef line, and fatigue makes her commitment visible.",
      "story_function": "Turns autonomy into a bodily choice in the exact surf moment.",
      "source_row_id": "seed-embry"
    }
  ],
  "asset_usage": [
    {
      "asset_id": "embry_media_asset__example_png",
      "memory_key": "embry_media_asset__example_png",
      "used_for": "Embry body posture, surfboard color, lava rock coastline, and mountain backdrop.",
      "source_field": "description"
    }
  ],
  "key_visual_motifs": [
    "Muted phones onshore",
    "Wax softened by heat",
    "Dark reef shapes under clear water"
  ],
  "important_sensory_details": [
    "Hot humid air",
    "Salt on skin",
    "Bright glare",
    "Paddle fatigue"
  ],
  "ending_image": "Embry and Kai float beyond the reef line while the muted phones wait in the beach bag.",
  "quality_checks": {
    "every_input_row_has_output_matrix_row": true,
    "environment_changes_character_or_object_behavior": true,
    "surfboards_include_shape_wax_condition_or_age_when_present": true,
    "asset_ids_are_copied_from_context": true,
    "all_eight_required_beats_present": true,
    "does_not_imitate_living_author": true
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
    "title",
    "logline",
    "tone",
    "author_style_directive",
    "creativity_directive",
    "main_dramatic_question",
    "character_arcs",
    "story_treatment",
    "interaction_matrix",
    "asset_usage",
    "key_visual_motifs",
    "important_sensory_details",
    "ending_image",
    "quality_checks"
  ],
  "properties": {
    "title": {
      "type": "string",
      "description": "Original title for the treatment."
    },
    "logline": {
      "type": "string",
      "description": "One sentence describing the stolen surf-day conflict."
    },
    "tone": {
      "type": "string",
      "description": "Concise tone statement."
    },
    "author_style_directive": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "requested_author",
        "style_policy",
        "style_summary",
        "actionable_traits",
        "application_to_this_story",
        "prohibited_imitation"
      ],
      "properties": {
        "requested_author": {
          "type": "string",
          "const": "Andy Weir"
        },
        "style_policy": {
          "type": "string",
          "description": "Explains that the author reference is translated into high-level craft traits only, not direct imitation."
        },
        "style_summary": {
          "type": "string"
        },
        "actionable_traits": {
          "type": "array",
          "minItems": 5,
          "items": {
            "type": "string"
          }
        },
        "application_to_this_story": {
          "type": "array",
          "minItems": 4,
          "items": {
            "type": "string"
          }
        },
        "prohibited_imitation": {
          "type": "array",
          "minItems": 3,
          "items": {
            "type": "string"
          }
        }
      }
    },
    "creativity_directive": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "slider_value",
        "label",
        "actionable_interpretation",
        "allowed_inventions",
        "limits",
        "plot_risk_level",
        "realism_requirement"
      ],
      "properties": {
        "slider_value": {
          "type": "number",
          "const": 0.6
        },
        "label": {
          "type": "string",
          "const": "grounded moderate invention"
        },
        "actionable_interpretation": {
          "type": "string"
        },
        "allowed_inventions": {
          "type": "array",
          "minItems": 4,
          "items": {
            "type": "string"
          }
        },
        "limits": {
          "type": "array",
          "minItems": 5,
          "items": {
            "type": "string"
          }
        },
        "plot_risk_level": {
          "type": "string",
          "enum": [
            "low",
            "moderate",
            "high"
          ],
          "const": "moderate"
        },
        "realism_requirement": {
          "type": "string"
        }
      }
    },
    "main_dramatic_question": {
      "type": "string",
      "description": "Question driving the scene."
    },
    "character_arcs": {
      "type": "array",
      "minItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "character",
          "starting_pressure",
          "turn",
          "ending_state"
        ],
        "properties": {
          "character": {
            "type": "string"
          },
          "starting_pressure": {
            "type": "string"
          },
          "turn": {
            "type": "string"
          },
          "ending_state": {
            "type": "string"
          }
        }
      }
    },
    "story_treatment": {
      "type": "array",
      "minItems": 8,
      "maxItems": 8,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "beat_id",
          "heading",
          "treatment"
        ],
        "properties": {
          "beat_id": {
            "type": "string",
            "enum": [
              "opening_image",
              "the_lie",
              "entering_the_water",
              "failed_or_hesitant_attempt",
              "kai_restraint",
              "mid_scene_tension",
              "decisive_set",
              "resolution"
            ]
          },
          "heading": {
            "type": "string"
          },
          "treatment": {
            "type": "string"
          }
        }
      }
    },
    "interaction_matrix": {
      "type": "array",
      "minItems": 7,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "entity",
          "objects",
          "environment_interaction",
          "story_function",
          "source_row_id"
        ],
        "properties": {
          "entity": {
            "type": "string",
            "description": "Copy from context.interaction_rows[].name."
          },
          "objects": {
            "type": "string",
            "description": "Physical objects copied or condensed from context.interaction_rows[].objects."
          },
          "environment_interaction": {
            "type": "string",
            "description": "How heat, humidity, water, reef, light, or fatigue changes this entity or object behavior."
          },
          "story_function": {
            "type": "string",
            "description": "How this row changes the story treatment."
          },
          "source_row_id": {
            "type": "string",
            "description": "Copy from context.interaction_rows[].id."
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
          "memory_key",
          "used_for",
          "source_field"
        ],
        "properties": {
          "asset_id": {
            "type": "string",
            "description": "Copy from context.linked_assets[].id."
          },
          "memory_key": {
            "type": [
              "string",
              "null"
            ],
            "description": "Copy from context.linked_assets[].memoryKey or null."
          },
          "used_for": {
            "type": "string",
            "description": "Specific visual, audio, video, or text grounding role in the story."
          },
          "source_field": {
            "type": "string",
            "enum": [
              "description",
              "title",
              "url",
              "mediaType"
            ]
          }
        }
      }
    },
    "key_visual_motifs": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "string"
      }
    },
    "important_sensory_details": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "string"
      }
    },
    "ending_image": {
      "type": "string",
      "description": "Final cinematic image of the treatment."
    },
    "quality_checks": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "every_input_row_has_output_matrix_row",
        "environment_changes_character_or_object_behavior",
        "surfboards_include_shape_wax_condition_or_age_when_present",
        "asset_ids_are_copied_from_context",
        "all_eight_required_beats_present",
        "does_not_imitate_living_author"
      ],
      "properties": {
        "every_input_row_has_output_matrix_row": {
          "type": "boolean"
        },
        "environment_changes_character_or_object_behavior": {
          "type": "boolean"
        },
        "surfboards_include_shape_wax_condition_or_age_when_present": {
          "type": "boolean"
        },
        "asset_ids_are_copied_from_context": {
          "type": "boolean"
        },
        "all_eight_required_beats_present": {
          "type": "boolean"
        },
        "does_not_imitate_living_author": {
          "type": "boolean"
        }
      }
    }
  }
}

Beat 1: Embry and Kai both faked a sick day at their summer jobs to go surfing on the Big Island on a Wednesday in June of 2024 — Kona Coast, Kahaluʻu Bay, summer swell patterns, lava rock reefs, local surf etiquette.
Location: Kahaluʻu Bay, Kona Coast · Wednesday · daylight surf window · June · 2024
Environment: Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.
Author: Andy Weir
Interaction matrix: [{"name":"Embry","objects":"navy rashguard, phone, family obligations, borrowed/older shortboard","dynamics":"Heat and humidity make her physically exposed: sweat, glare, and tired paddling turn autonomy into a bodily choice, not just an idea.","note":"Script/panels should show sweat, squinting, salt on skin, careful hand placement, and fatigue in her paddle cadence before dialogue explains anything.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-0","isComplete":true},{"name":"Kai","objects":"black rashguard, phone call, surf ritual, familiar shortboard","dynamics":"Reads the swell while managing heat, glare, and patience; his competence shows in conserving effort instead of forcing the moment.","note":"Stage Kai as physically adapted to the heat: calm breathing, economical paddling, shaded glances at the reef line, and small gestures that guide Embry without lecturing.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-1","isComplete":true},{"name":"Embry surfboard","objects":"White shortboard, performance shape, visibly waxed deck, likely older/borrowed, rail pressure matters over shallow reef.","dynamics":"Humidity and sun soften wax and make footing less certain; the board forces Embry to commit cleanly despite tired arms and slick contact points.","note":"Panel details should include wax smears, sun glare on the deck, hands gripping rails, and foot placement uncertainty as the board reacts to chop and reef proximity.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-2","isComplete":true},{"name":"Kai surfboard","objects":"White shortboard with darker underside/rail marks, well-used and waxed, familiar enough for quick reef-line decisions.","dynamics":"A waxed, familiar board lets Kai compensate for heat, chop, and glare; restraint is visible when he waits rather than wasting energy.","note":"Use the board as proof of familiarity: worn rail marks, confident trim angle, efficient turns, and quick corrections under humid, high-glare conditions.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-3","isComplete":true},{"name":"June Swell","objects":"sets, tide window, wave face","dynamics":"Creates the timing pressure that makes hesitation and trust visible.","note":"Panels need repeating set rhythm: quiet water, approaching lump, glare on the face, then a fast decision point.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-4","isComplete":true},{"name":"Lava Reef","objects":"sharp rock, shallow line, safe channel","dynamics":"Turns the environment into a hard boundary rather than background scenery.","note":"Show the reef as a physical rule: dark shapes below clear water, shallow consequences, and characters adjusting line and timing around it.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-5","isComplete":true},{"name":"Kona Coast","objects":"bay, local etiquette, reef break","dynamics":"Holds the scene inside a public place where local rules shape private choices.","note":"Script beats should include public beach pressure, waiting turns, reading locals, and the contrast between private escape and shared water.","environment":"Hot, humid coastal air with summer swell patterns, lava rock reef constraints; sweat, glare, wax softness, saltwater, and fatigue change grip, footing, board control, reef caution, and social patience.","id":"seed-6","isComplete":true}]
Linked assets: [{"id":"embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png","title":"Embry surfing on the Big Island","url":"/api/persona-media?persona=embry&path=assets%2Fsurfing%2Fembry_surfing_big_island_2024.png","description":"Embry, a young woman in a navy surf top, crouches low on a white surfboard carving across a small wave, one hand brushing the water as spray bursts around her. Golden light hits the Big Island coastline behind her, with palm trees, dark lava rocks, green mountains, and low clouds creating a dramatic tropical backdrop for Kai to spot her ride or call out from nearby.","source":"embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png","memoryKey":"embry_media_asset__assets_surfing_embry_surfing_big_island_2024_png","mediaType":"png"},{"id":"embry_media_asset__assets_character_sheet_montage_jpg","title":"Embry image media asset character sheet montage (assets). Asset path /mnt/storage12tb/media/personas/embry/assets/character sheet montage.jpg Relative path assets/character sheet montage.jpg Media","url":"/api/persona-media?persona=embry&path=assets%2Fcharacter_sheet_montage.jpg","description":"Embry is a young woman with brown hair tied back, expressive green-brown eyes, and a navy polo, shown in multiple reference poses: neutral, focused at a computer, explaining with her hand raised, tired in a hoodie, smiling outdoors, and working at a multi-monitor desk with notes, mugs, and office items. The settings alternate between bright office interiors and warm coastal balcony/beach views with palm trees, ocean, and golden sunlight, establishing her as a thoughtful tech-savvy character ready to enter a 10-second Embry/Kai surfing story.","source":"embry_media_asset__assets_character_sheet_montage_jpg","memoryKey":"embry_media_asset__assets_character_sheet_montage_jpg","mediaType":"jpg"},{"id":"embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png","title":"Embry barrel wave surfing reference","url":"/api/persona-media?persona=embry&path=assets%2Fsurfing%2Fembry_barrel_wave_big_island_2024.png","description":"Embry/Kai is a young surfer in a dark blue shirt crouched low on a white surfboard, riding inside a curling turquoise barrel wave with one hand skimming the water for balance. Warm golden light hits her focused face as spray arcs overhead, with tropical palms, green mountains, rocky shore, and partly cloudy sky visible beyond the wave.","source":"embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png","memoryKey":"embry_media_asset__assets_surfing_embry_barrel_wave_big_island_2024_png","mediaType":"png"},{"id":"kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png","title":"Kai Akana surfing on the Big Island","url":"/api/persona-media?persona=kai_akana&path=assets%2Fsurfing%2Fkai_surfing_big_island_2024.png","description":"Kai Akana, a young surfer in a black rash guard and board shorts, carves low on a small glassy wave as spray bursts around his white surfboard. Behind him, the Big Island coastline shows dark lava rocks, palm trees, and steep green mountains under warm golden light with low clouds, setting up a focused surfing moment for Embry/Kai.","source":"kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png","memoryKey":"kai_akana_media_asset__assets_surfing_kai_surfing_big_island_2024_png","mediaType":"png"},{"id":"kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png","title":"Kai Akana character sheet","url":"/api/persona-media?persona=kai_akana&path=assets%2Fcontact_sheets%2Fkai_akana_character_sheet.png","description":"Kai Akana is a young Hawaiian/Norwegian/Japanese surfer with tan skin, curly dark hair, athletic build, and expressive brown eyes, shown in navy tees, a black wetsuit, and post-surf shirtless with a white surfboard. Use him on a bright tropical beach with blue ocean, green mountains, palms, and warm sun, actively carrying or steadying his board after a surf session while looking toward Embry with an easy, engaged smile.","source":"kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png","memoryKey":"kai_akana_media_asset__assets_contact_sheets_kai_akana_character_sheet_png","mediaType":"png"}]
