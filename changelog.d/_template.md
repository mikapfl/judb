{#-
══════════════════════════════════════════════════════════════════════════════
judb changelog template — vendored from towncrier's builtin `default.md`
(towncrier 25.8.0), but a category heading is only emitted when
the category has at least one renderable entry.
══════════════════════════════════════════════════════════════════════════════

─── Macro: heading ─────────────────────────────────────────────────────────
Generates Markdown headings with the appropriate number of # characters,
based on header_prefix (default: "#") and the level argument.
-#}
{%- macro heading(level) -%}
    {{- "#" * ( header_prefix | length + level -1 ) }}
{%- endmacro -%}

{%- set newline = "\n" -%}

{#- ════════════════════════ TEMPLATE GENERATION ════════════════════════ -#}
{#- ─── TITLE HEADING ─── #}
{%- if render_title %}
    {%- if versiondata.name %}
        {{- heading(1) ~ " " ~ versiondata.name ~ " " ~ versiondata.version ~ " (" ~ versiondata.date ~ ")" ~ newline }}
    {%- else %}
        {{- heading(1) ~ " " ~ versiondata.version ~ " (" ~ versiondata.date ~ ")" ~ newline }}
    {%- endif %}
{%- endif %}
{#- If title_format is specified, we start with a new line #}
{{- newline }}

{%- for section, _ in sections.items() %}
    {#- ─── SECTION HEADING ─── #}
    {%- if section %}
        {{- newline }}
        {{- heading(2) ~ " " ~ section ~ newline }}
        {{- newline }}
    {%- endif %}

    {%- if sections[section] %}

        {%- for category, val in definitions.items() if category in sections[section] %}

            {#- ─── judb: does this category have anything to render? ─── #}
            {%- set ns = namespace(has_content=false) %}
            {%- for text, values in sections[section][category].items() %}
                {%- set issue_pks = [] %}
                {%- for v_issue in values %}
                    {%- set _= issue_pks.append(v_issue.split(": ", 1)[0]) %}
                {%- endfor %}
                {%- if text or (issue_pks | join(", ")) %}
                    {%- set ns.has_content = true %}
                {%- endif %}
            {%- endfor %}

            {%- if ns.has_content %}
                {#- ─── CATEGORY HEADING (increase level if section is absent) ─── #}
                {{- heading(3 if section else 2) ~" " ~ definitions[category]['name'] ~ newline }}
                {{- newline }}

                {#- ─── RENDER ENTRIES ─── #}
                {%- for text, values in sections[section][category].items() %}
                    {%- set issue_pks = [] %}
                    {%- for v_issue in values %}
                        {%- set _= issue_pks.append(v_issue.split(": ", 1)[0]) %}
                    {%- endfor %}
                    {%- set issues_list = issue_pks | join(", ") %}

                    {%- set text_has_sublist = (("\n  - " in text) or ("\n  * " in text)) %}

                    {#- CASE 1: No text, only issues → "-  #1, #9" #}
                    {%- if not text and issues_list %}
                        {{- "- " ~ issues_list ~ newline }}

                    {#- Both text and issues #}
                    {%- elif text and issues_list %}
                        {%- if text_has_sublist %}
                            {{- "- " ~ text ~ newline ~ newline ~ "  (" ~ issues_list ~ ")" ~ newline }}
                        {%- else %}
                            {{- "- " ~ text ~ " (" ~ issues_list ~ ")" ~ newline }}
                        {%- endif %}

                    {%- elif text %}
                        {{- "- " ~ text ~ newline }}
                    {%- endif %}
                {%- endfor %}

                {#- New line between list and link references #}
                {{- newline }}

                {#- Link references #}
                {%- if issues_by_category[section][category] and "]: " in issues_by_category[section][category][0] %}
                    {%- for issue in issues_by_category[section][category] %}
                        {{- issue ~ newline }}
                    {%- endfor %}
                    {{- newline }}
                {%- endif %}
            {%- endif %}
        {%- endfor %}
    {%- else %}
        {#- No changes in this section #}
        {{- "No significant changes." ~ newline * 2 }}
    {%- endif %}
{%- endfor %}
{{- newline -}}
