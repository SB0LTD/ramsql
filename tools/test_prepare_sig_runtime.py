"""Synthetic archive fixtures validate extraction/integrity, never runtime evidence."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from prepare_sig_runtime import extract_verified,verify_stdlib


class PreparationContracts(unittest.TestCase):
    def fixture(self,root,extra=()):
        archive=root/'runtime.tar.gz';prefix='sig-'+'a'*40
        with tarfile.open(archive,'w:gz') as output:
            for path,data in [('lib/std/std.sig',b'synthetic stdlib fixture'),('lib/sig/sig.sig',b'fixture'),('tools/sig_build/main.sig',b'fixture'),*extra]:
                info=tarfile.TarInfo(prefix+'/'+path);info.size=len(data);output.addfile(info,io.BytesIO(data))
        return archive,dict(archive='https://github.com/SB0LTD/sig/archive/'+'a'*40+'.tar.gz',sha256=hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_verified_extract_and_matching_released_stdlib(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive,pin=self.fixture(root)
            prepared=extract_verified(archive,root/'prepared',pin)
            installed=root/'installed/std';installed.mkdir(parents=True)
            (installed/'std.sig').write_bytes(b'synthetic stdlib fixture')
            proof=verify_stdlib(installed.parent,prepared)
            self.assertEqual(proof['matched_stdlib_files'],1)
            with self.assertRaisesRegex(ValueError,'already exists'):extract_verified(archive,root/'prepared',pin)
            (installed/'std.sig').write_bytes(b'different released source')
            with self.assertRaisesRegex(ValueError,'changes released'):verify_stdlib(installed.parent,prepared)

    def test_hash_mismatch_precedes_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive,pin=self.fixture(root);pin['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'checksum'):extract_verified(archive,root/'prepared',pin)
            self.assertFalse((root/'prepared').exists())

    def test_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive,pin=self.fixture(root,[('lib/../../escaped',b'unsafe')])
            with self.assertRaisesRegex(ValueError,'unsafe'):extract_verified(archive,root/'prepared',pin)
            self.assertFalse((root/'escaped').exists())

    def test_symlink_in_runtime_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive,pin=self.fixture(root)
            with tarfile.open(archive,'w:gz') as output:
                info=tarfile.TarInfo('sig-'+'a'*40+'/lib/redirect');info.type=tarfile.SYMTYPE;info.linkname='outside';output.addfile(info)
            pin['sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'regular files'):extract_verified(archive,root/'prepared',pin)


if __name__=='__main__':unittest.main(verbosity=2)
