from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.app.config.logging import StructuredLogger
from backend.app.config.settings import Settings


@dataclass
class PatchResult:
    success: bool
    path: str
    new_content: str | None = None
    commit_message: str = ""
    reason: str = ""


class PatchGenerator:
    """Service for generating and validating targeted patches on verified repository files."""

    def __init__(self, settings: Settings, logger: StructuredLogger | None = None) -> None:
        self._settings = settings
        self._logger = logger or StructuredLogger(settings, name="opspilot.patch_generator")

    def rank_candidate_files(
        self,
        verified_paths: list[str],
        goal: str,
        issue_title: str,
        issue_body: str,
    ) -> list[str]:
        """Rank verified candidate files by relevance to the goal and issue."""
        if not verified_paths:
            return []

        text = f"{goal} {issue_title} {issue_body}".lower()
        keywords = set(re.findall(r"\b[a-z0-9_]{3,}\b", text))

        def score_path(path: str) -> float:
            p_low = path.lower()
            score = 0.0
            for kw in keywords:
                if kw in p_low:
                    score += 2.0
            if any(p_low.endswith(ext) for ext in [".py", ".ts", ".js", ".go", ".rs"]):
                score += 1.5
            elif any(p_low.endswith(ext) for ext in [".txt", ".toml", ".json", ".yaml", ".yml"]):
                score += 1.0
            if "readme" in p_low or "license" in p_low or "changelog" in p_low:
                score -= 3.0
            return score

        ranked = sorted(verified_paths, key=score_path, reverse=True)
        return ranked

    async def generate_patch(
        self,
        goal: str,
        issue: dict[str, Any],
        target_path: str,
        original_content: str,
        verified_paths: list[str],
    ) -> PatchResult:
        """Generate a validated targeted patch for a verified file."""

        # Target path MUST be in verified_paths
        if target_path not in verified_paths:
            return PatchResult(
                success=False,
                path=target_path,
                reason=f"Target path '{target_path}' is not in verified file paths.",
            )

        if original_content is None:
            return PatchResult(
                success=False,
                path=target_path,
                reason=f"Original content for '{target_path}' is unavailable.",
            )

        issue_title = issue.get("title", "")
        issue_body = issue.get("body", "")
        issue_num = issue.get("number", 0)

        # 1. Try LLM generation if Gemini API key is configured
        if self._settings.gemini_api_key:
            llm_result = await self._generate_patch_llm(
                goal=goal,
                issue_title=issue_title,
                issue_body=issue_body,
                issue_num=issue_num,
                target_path=target_path,
                original_content=original_content,
            )
            if llm_result.success:
                valid, validation_reason = self.validate_patch(
                    target_path=target_path,
                    original_content=original_content,
                    new_content=llm_result.new_content or "",
                    verified_paths=verified_paths,
                )
                if valid:
                    return llm_result
                else:
                    self._logger.warning(
                        f"LLM generated patch failed validation: {validation_reason}",
                        path=target_path,
                    )

        # 2. Fallback to safe deterministic rules for known patterns
        det_result = self._generate_patch_deterministic(
            goal=goal,
            issue=issue,
            target_path=target_path,
            original_content=original_content,
        )
        if det_result.success:
            valid, validation_reason = self.validate_patch(
                target_path=target_path,
                original_content=original_content,
                new_content=det_result.new_content or "",
                verified_paths=verified_paths,
            )
            if valid:
                return det_result
            else:
                return PatchResult(
                    success=False,
                    path=target_path,
                    reason=f"Deterministic patch failed validation: {validation_reason}",
                )

        return PatchResult(
            success=False,
            path=target_path,
            reason=f"OpsPilot could not generate a safe targeted patch from verified repository content for '{target_path}'.",
        )

    async def _generate_patch_llm(
        self,
        goal: str,
        issue_title: str,
        issue_body: str,
        issue_num: int,
        target_path: str,
        original_content: str,
    ) -> PatchResult:
        try:
            from google import genai

            client = genai.Client(api_key=self._settings.gemini_api_key)
            prompt = (
                f"You are OpsPilot, an autonomous engineering agent.\n"
                f"Goal: {goal}\n"
                f"Issue #{issue_num}: {issue_title}\n"
                f"Description: {issue_body}\n\n"
                f"Target File Path: {target_path}\n"
                f"Current File Content:\n```\n{original_content}\n```\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Make the smallest, most targeted fix to resolve the issue.\n"
                f"2. Preserve all existing docstrings, comments, imports, and unrelated code.\n"
                f"3. Do NOT add placeholder text (such as TODOs, placeholders, or dummy comments).\n"
                f"4. Do NOT wipe or truncate unrelated file sections.\n"
                f"5. Return ONLY the complete updated file content inside ``` code block."
            )

            response = client.models.generate_content(
                model=self._settings.gemini_model,
                contents=prompt,
            )

            text = response.text or ""
            code_match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
            new_content = code_match.group(1) if code_match else text.strip()

            msg = f"fix: resolve issue #{issue_num} in {target_path}"

            return PatchResult(
                success=True,
                path=target_path,
                new_content=new_content,
                commit_message=msg,
            )
        except Exception as exc:
            self._logger.warning(f"LLM patch generation error: {exc}")
            return PatchResult(
                success=False,
                path=target_path,
                reason=f"LLM patch generation failed: {exc}",
            )

    def _generate_patch_deterministic(
        self,
        goal: str,
        issue: dict[str, Any],
        target_path: str,
        original_content: str,
    ) -> PatchResult:
        issue_title = (issue.get("title") or "").lower()
        issue_body = (issue.get("body") or "").lower()
        issue_num = issue.get("number", 0)
        p_low = target_path.lower()
        comb_text = (issue_title + " " + issue_body + " " + goal.lower()).lower()

        # 1. Dependency files (e.g. requirements.txt / pyproject.toml)
        if "requirements.txt" in p_low or "pyproject.toml" in p_low:
            if "httpx" in comb_text:
                if "httpx" in original_content:
                    new_c = re.sub(r"httpx\s*==\s*[\d\.]+", "httpx==0.27.2", original_content)
                    if new_c == original_content:
                        new_c = original_content.rstrip() + "\nhttpx==0.27.2\n"
                    return PatchResult(
                        success=True,
                        path=target_path,
                        new_content=new_c,
                        commit_message=f"chore(deps): upgrade httpx in {target_path} (closes #{issue_num})",
                    )

        # 2. Targeted source fixes (e.g. token/auth timeout or leeway adjustments)
        if ("timeout" in comb_text or "leeway" in comb_text or "auth" in comb_text) and ("token" in p_low or "auth" in p_low):
            if "timeout" in original_content or "leeway" in original_content or "expire" in original_content or "def " in original_content:
                new_c = original_content
                if "timeout" in new_c and re.search(r"timeout\s*=\s*\d+", new_c):
                    new_c = re.sub(r"timeout\s*=\s*\d+", "timeout=30", new_c)
                if "leeway" in new_c and re.search(r"leeway\s*=\s*\d+", new_c):
                    new_c = re.sub(r"leeway\s*=\s*\d+", "leeway=10", new_c)

                # Targeted fix for token timeout/validation if regex didn't change anything but timeout issue requested
                if new_c == original_content and ("timeout" in comb_text or "handling" in comb_text):
                    # Apply minimal targeted parameter fix if pattern matches
                    if "def verify_token(" in original_content or "def validate_token(" in original_content:
                        new_c = re.sub(
                            r"(def (?:verify|validate)_token\([^)]*)\):",
                            r"\1, leeway: int = 10):",
                            original_content
                        )

                if new_c != original_content:
                    return PatchResult(
                        success=True,
                        path=target_path,
                        new_content=new_c,
                        commit_message=f"fix: update timeout and leeway handling in {target_path} (closes #{issue_num})",
                    )

        return PatchResult(
            success=False,
            path=target_path,
            reason=f"No deterministic patch pattern matched for {target_path}.",
        )

    def validate_patch(
        self,
        target_path: str,
        original_content: str,
        new_content: str,
        verified_paths: list[str],
    ) -> tuple[bool, str]:
        """Validate a generated patch against strict safety requirements."""

        # 1. Target path is in verified_file_paths
        if target_path not in verified_paths:
            return False, f"Target path '{target_path}' is not in verified candidate paths."

        # 10. No path traversal or leading slash / absolute path
        if ".." in target_path or target_path.startswith("/") or target_path.startswith("\\") or bool(re.match(r"^[a-zA-Z]:", target_path)):
            return False, f"Invalid target path '{target_path}': path traversal or absolute path detected."

        # 4. New content differs from original
        if new_content == original_content:
            return False, "Generated content is identical to original content."

        # 3. New content is non-empty when original is non-empty
        if original_content.strip() and not new_content.strip():
            return False, "Generated content is empty while original content was non-empty."

        # 5. Generated content does not look like placeholder output
        placeholder_indicators = [
            "leeway-aware fix placeholder",
            "autonomous improvement placeholder",
            "todo: implement",
            "# placeholder",
            "// placeholder",
            "placeholder fix",
        ]
        low_new = new_content.lower()
        for indicator in placeholder_indicators:
            if indicator in low_new:
                return False, f"Generated content contains placeholder text ('{indicator}')."

        # 6. Generated content does not unexpectedly remove most of the file (destructive diff guard)
        old_lines = original_content.splitlines()
        new_lines = new_content.splitlines()
        if len(old_lines) >= 5 and len(new_lines) < len(old_lines) * 0.5:
            return False, f"Destructive change detected: line count shrank from {len(old_lines)} to {len(new_lines)}."

        # 7. Preserves unrelated content / file structure
        if len(old_lines) >= 10 and len(new_lines) < 3:
            return False, "Generated patch truncates file excessively."

        return True, "Patch validation passed."
