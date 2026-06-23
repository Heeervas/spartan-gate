import pathlib
import unittest


HERMES_DIR = pathlib.Path(__file__).parent
DOCKERFILE_PATH = HERMES_DIR / 'Dockerfile'
ENTRYPOINT_PATH = HERMES_DIR / 'entrypoint-wrapper.sh'
PATCH_PATH = HERMES_DIR / 'patches' / 'patch_image_generate_quality.py'


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


class HermesImageGenerateQualityPatchTests(unittest.TestCase):
    def test_obsolete_patch_script_is_removed(self):
        self.assertFalse(PATCH_PATH.exists())

    def test_dockerfile_does_not_run_quality_patch(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertNotIn('patch_image_generate_quality.py', dockerfile)

    def test_entrypoint_does_not_reapply_quality_patch(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertNotIn('patch_image_generate_quality.py', entrypoint)


if __name__ == '__main__':
    unittest.main()
