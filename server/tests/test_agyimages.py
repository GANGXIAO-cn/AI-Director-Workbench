from pathlib import Path
from types import SimpleNamespace

from workbench.agyimages import AntigravityImageProvider
from workbench.images import image_size, png_bytes
from workbench.storage import sha256_bytes


def test_reference_paths_and_recovery_return_the_original_artifact(tmp_path):
    marker="adw_"+sha256_bytes(b"sample")[:16]
    inputs=[]
    for n in (1,2):
        path=tmp_path/str(n)
        path.write_bytes(png_bytes(16,16,(n,n,n)))
        inputs.append(str(path))
    calls=[]
    def start(request):
        calls.append(request)
        for i,src in enumerate(inputs,1):
            ref=request.workdir/f"ref-{i}.png"
            assert str(ref) in request.prompt and ref.read_bytes()==Path(src).read_bytes()
        file=request.workdir/(marker+".png")
        file.write_bytes(png_bytes(32,32))
        return SimpleNamespace(ok=True, structured={'imagePath':str(file)}, session_id='unit')
    provider=AntigravityImageProvider(tmp_path/"runs", SimpleNamespace(binary=lambda:True,start=start))
    provider.connection=lambda: dict(model='selected-model',effort='medium')
    req=dict(local_request_id='sample',size='1024x1024',inputs=inputs,prompt='保留造型')
    assert image_size(provider.submit(req).data)==(1024,1024)
    assert (calls[0].model,calls[0].effort)==('selected-model','medium')
    assert provider.submit(req).provider_request_id=='unit' and len(calls)==1
