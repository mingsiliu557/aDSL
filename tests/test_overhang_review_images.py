import asyncio
import json
from types import SimpleNamespace

import pytest

from adsl.agents.overhang_edit import review_images
from adsl.agents.service import ObjectWorkflow
from adsl.agents.models import ImageCriticDecision, CodeCriticDecision


def views(tmp_path, name, contents):
    directory = tmp_path / name
    directory.mkdir()
    paths = []
    for i, content in enumerate(contents):
        path = directory / f'{i}.png'
        path.write_bytes(content)
        paths.append(path)
    return tuple(paths)


def test_initial_twenty_inputs_keep_all_eight_unique_views(tmp_path):
    content = [f'view{i}'.encode() for i in range(8)]
    reference = views(tmp_path, 'references', content[:4])
    baseline = views(tmp_path, 'baseline', content)
    candidate = views(tmp_path, 'candidate', content)
    images, mapping = review_images(reference, baseline, candidate)
    assert len(images) == 8
    assert mapping['reference_indices'] == [1, 2, 3, 4]
    assert mapping['baseline_indices'] == mapping['candidate_indices'] == list(range(1, 9))
    assert [p.read_bytes() for p in images] == content


def test_changed_candidate_images_never_disappear(tmp_path):
    baseline = views(tmp_path, 'baseline', [b'front', b'back'])
    candidate = views(tmp_path, 'candidate', [b'changed front', b'back'])
    images, mapping = review_images((), baseline, candidate)
    assert len(images) == 3
    assert mapping['baseline_indices'] == [1, 2]
    assert mapping['candidate_indices'] == [3, 2]
    assert images[2].read_bytes() == b'changed front'
    assert review_images((), (), ())[0] == ()
    with pytest.raises(FileNotFoundError):
        review_images((), (), [tmp_path / 'missing.png'])


@pytest.mark.parametrize('experiment', [{}, {'arm': 'feedback'}, {'arm': 'control'}])
def test_both_critics_use_same_mapping_and_normal_path_unchanged(tmp_path, experiment):
    baseline = views(tmp_path, 'baseline', [b'front', b'back'])
    candidate = views(tmp_path, 'candidate', [b'changed front', b'back'])
    reference = views(tmp_path, 'reference', [b'front'])
    source = tmp_path / 'source.py'
    source.write_text('scene = None\n')
    calls = []
    async def run(**kw):
        content = kw['input'][0]['content']
        payload = json.loads(content[0]['text'])
        calls.append((payload, content[1:]))
        if kw['role'].startswith('image-critic'):
            output = ImageCriticDecision(approved=False, observations=['possible visual issue'])
        else:
            kw['context'].record('read_file', source)
            output = CodeCriticDecision(approved=True, observations=['source inspected'],
                                       image_critic_corrections=['visual issue not supported by source'])
        return SimpleNamespace(final_output=output)
    approved, reviews = asyncio.run(ObjectWorkflow()._review_candidate_appearance(
        runtime=SimpleNamespace(run=run), request=SimpleNamespace(overhang_experiment=experiment,
            image_paths=reference, requirement='preserve chair'), workspace=tmp_path,
        round_number=1, proposal_index=0, proposal=SimpleNamespace(model_dump=lambda: {}),
        baseline_execution=SimpleNamespace(render_paths=baseline),
        candidate_execution=SimpleNamespace(render_paths=candidate), candidate_source=source,
        candidate_root=tmp_path, image_critic=None, code_critic=None))
    assert approved and reviews['code_critic']['image_critic_corrections']
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert calls[0][0]['image_order'] == calls[1][0]['image_order']
    if experiment:
        assert len(calls[0][1]) == 3
        assert calls[0][0]['image_order']['candidate_indices'] == [3, 2]
        assert 'baseline_count' not in calls[0][0]['image_order']
        assert reviews['image_input_mapping'] == json.loads((tmp_path / 'image_input_mapping.json').read_text())
    else:
        assert len(calls[0][1]) == 5
        assert calls[0][0]['image_order'] == {'reference_count': 1, 'baseline_count': 2, 'candidate_count': 2}
        assert not (tmp_path / 'image_input_mapping.json').exists()
        assert 'image_input_mapping' not in reviews
