"""In-memory tar archive builder and code patch applicator for isolated container injection."""

import io
import re
import tarfile
import time

from agent_core.agent.state import FilePatch


class SandboxPatcher:
    """Manages in-memory file patching, tar streaming, and test failure parsing."""

    FAILED_TEST_PATTERN = re.compile(r"FAILED\s+([^\s:]+(?:::[\w\[\]\-]+)+)")

    @classmethod
    def apply_patches(
        cls,
        base_files: dict[str, str],
        patches: list[FilePatch],
    ) -> dict[str, str]:
        """Applies a list of FilePatch objects to a dictionary of file contents.

        Returns a new dictionary containing the updated file contents.
        Raises ValueError if a target original_snippet cannot be located.
        """
        updated_files = dict(base_files)

        for patch in patches:
            path = patch.file_path.strip().lstrip("/")
            if path in updated_files:
                original_content = updated_files[path]
                if patch.original_snippet not in original_content:
                    raise ValueError(
                        f"Original snippet for patch in '{path}' not found in target file."
                    )
                updated_files[path] = original_content.replace(
                    patch.original_snippet,
                    patch.replacement_snippet,
                    1,
                )
            else:
                # New file creation
                updated_files[path] = patch.replacement_snippet

        return updated_files

    @classmethod
    def create_tar_archive(
        cls,
        files: dict[str, str],
        uid: int = 1000,
        gid: int = 1000,
    ) -> bytes:
        """Packages a dictionary of relative file paths and text contents into an in-memory tar.

        All files and directories are tagged with non-root ownership and standard permissions.
        """
        buffer = io.BytesIO()
        now = time.time()

        with tarfile.open(fileobj=buffer, mode="w") as tar:
            created_dirs: set[str] = set()

            for file_path, content in files.items():
                normalized_path = file_path.strip().lstrip("/")
                parts = normalized_path.split("/")

                # Create parent directory entries if needed
                for i in range(1, len(parts)):
                    dir_path = "/".join(parts[:i])
                    if dir_path not in created_dirs:
                        dir_info = tarfile.TarInfo(name=dir_path)
                        dir_info.type = tarfile.DIRTYPE
                        dir_info.mode = 0o755
                        dir_info.mtime = int(now)
                        dir_info.uid = uid
                        dir_info.gid = gid
                        tar.addfile(dir_info)
                        created_dirs.add(dir_path)

                # Add file entry
                data = content.encode("utf-8")
                tar_info = tarfile.TarInfo(name=normalized_path)
                tar_info.size = len(data)
                tar_info.mode = 0o644
                tar_info.mtime = int(now)
                tar_info.uid = uid
                tar_info.gid = gid
                tar.addfile(tar_info, fileobj=io.BytesIO(data))

        return buffer.getvalue()

    @classmethod
    def extract_tar_archive(cls, tar_bytes: bytes) -> dict[str, str]:
        """Extracts an in-memory tar archive back into a file path to content mapping."""
        buffer = io.BytesIO(tar_bytes)
        files: dict[str, str] = {}

        with tarfile.open(fileobj=buffer, mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f is not None:
                        files[member.name] = f.read().decode("utf-8")

        return files

    @classmethod
    def extract_failing_tests(cls, stdout: str, stderr: str) -> list[str]:
        """Extracts failing test node IDs from pytest console output."""
        combined = f"{stdout}\n{stderr}"
        matches = cls.FAILED_TEST_PATTERN.findall(combined)
        return sorted(list(set(matches)))
