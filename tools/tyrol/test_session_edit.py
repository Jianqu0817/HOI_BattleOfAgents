"""Audio-filter flush regression; run with python tools/tyrol/test_session_edit.py."""
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from session_edit import complete_audio


class CompleteAudioTest(unittest.TestCase):
    def test_normalization_keeps_the_last_spoken_samples(self):
        # Longer than loudnorm's lookahead: the old video-frame-limited pass
        # silently discarded almost three seconds at the end of such clips.
        with tempfile.TemporaryDirectory() as directory:
            source, target = (Path(directory)/name for name in ('source.wav','fixed.wav'))
            t = np.arange(8*24000)/24000
            pcm = (8000*np.sin(2*np.pi*440*t)).astype('<i2')
            with wave.open(str(source),'wb') as audio:
                audio.setparams((1,2,24000,0,'NONE','not compressed'))
                audio.writeframes(pcm.tobytes())
            complete_audio(source,target,200)
            with wave.open(str(target)) as audio:
                self.assertEqual(audio.getnframes(),384000)
                self.assertEqual(audio.getframerate(),48000)
                self.assertEqual(audio.getnchannels(),2)
                audio.setpos(384000-4800)
                tail=np.frombuffer(audio.readframes(4800),'<i2').astype(float)
                self.assertGreater(np.sqrt(np.mean(tail**2)),500)


if __name__ == '__main__':
    unittest.main()
