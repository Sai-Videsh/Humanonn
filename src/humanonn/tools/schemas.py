from __future__ import annotations


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "crawl_page",
            "description": "Crawl the homepage and return rendered style, structure, copy, accessibility hints, and screenshot path.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_elements",
            "description": "Inspect already crawled element groups such as buttons, inputs, or links.",
            "parameters": {
                "type": "object",
                "properties": {"selectors": {"type": "array", "items": {"type": "string"}}},
                "required": ["selectors"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_layout",
            "description": "Summarize section structure, grid usage, card-like surfaces, and heading alignment.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_accessibility",
            "description": "Check focus, labels, alt text, and tooltip/accessibility hints.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_copy",
            "description": "Analyze CTA, heading, badge, and visible copy patterns.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Generate the final Humanonn report from accumulated findings.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

