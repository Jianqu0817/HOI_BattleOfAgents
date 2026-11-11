"""Regression for non-uniform native word timing at the trailer/classroom cut."""
import unittest
import numpy as np

from integrate_v3_opening import native_bounds,insert_silences,SPF


class NativeSubtitleTimingTest(unittest.TestCase):
    def test_uses_word_times_not_relative_caption_lengths(self):
        parts=['你将扮演一位名为国家意志的神秘角色，',
               '在第二次世界大战中，邂逅国策各异、',
               '民族精神独特的色块们——','后面还有抽卡。','行，这稿就念到这儿。']
        starts=[0,16920,20280,22960,24840]
        words=[];offset=0
        for part,when in zip(parts,starts):
            words.append({'word':part[0],'word_begin':offset,'word_end':offset+1,
                          'time_begin':when,'time_end':when+100})
            offset+=len(part)
        result=native_bounds(parts,[{'timestamped_words':words}],691)
        self.assertEqual(result,[0,423,507,574,621,691])
        # The first three parts differ in length, but timing must follow the audio.
        self.assertEqual(result[1]/25,16.92)
        self.assertEqual(result[-2]/25,24.84)

    def test_pauses_preserve_every_original_sample_and_shift_cues(self):
        pcm=np.arange(8*SPF,dtype='<i2')
        result,shifted=insert_silences(pcm,[(2,3,'hold'),(6,2,'reaction')])
        self.assertEqual(len(result),13*SPF)
        np.testing.assert_array_equal(result[:2*SPF],pcm[:2*SPF])
        self.assertFalse(result[2*SPF:5*SPF].any())
        np.testing.assert_array_equal(result[5*SPF:9*SPF],pcm[2*SPF:6*SPF])
        self.assertFalse(result[9*SPF:11*SPF].any())
        np.testing.assert_array_equal(result[11*SPF:],pcm[6*SPF:])
        self.assertEqual([shifted(i) for i in [0,2,5,6,8]],[0,5,8,11,13])


if __name__=='__main__':unittest.main()
