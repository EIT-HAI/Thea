# Images and Named Views

## Observation output

The recommended channel path uses named visual evidence published by the
Harness:

```json
{
  "type": "observation",
  "visual_outputs": [
    {
      "view": "front",
      "label": "front",
      "image_url": {"url": "data:image/png;base64,..."},
      "caption": "Front camera"
    }
  ]
}
```

Trusted tools and providers may also emit `visual_outputs` containing a URL or
`image_path`. The channel preserves `color_space` and `color_order` metadata.

## `query_user` attachments

The default model-facing schema accepts a question, optional candidate Scene
Graph refs, and optional named current views:

```json
{
  "question": "Which bottle do you mean?",
  "candidate_refs": ["bottle_12", "bottle_18"],
  "observation_views": ["front", "wrist"]
}
```

Candidate refs are displayed as structured choices before the attached views.
Names resolve only from the latest Observation's `visual_outputs`. A view name
can be carried in `view`, `camera`, `label`, or `caption`. Matching is
case-insensitive and ignores punctuation. Missing views return a Tool Result
failure instead of attaching stale or unrelated evidence.

Use short identifiers such as `front`, `torso`, and `wrist`.

## Encoded images and NPY arrays

Encoded PNG and JPEG files are forwarded without channel swapping. NPY arrays
default to RGB or RGBA. Only an NPY-producing output explicitly marked `BGR`
or `BGRA` has red and blue channels swapped:

```json
{
  "view": "front",
  "image_path": "./runtime/images/front.npy",
  "color_space": "BGR"
}
```

Supported NPY arrays are non-Fortran-order `uint8` values shaped:

- `H x W`;
- `H x W x 3`;
- `H x W x 4`.

Do not add BGR metadata to encoded PNG or JPEG images.

## Local files

The default `query_user` schema does not accept arbitrary paths or URLs.
Trusted provider paths remain disabled unless `LARK_IMAGE_ALLOWED_ROOT` names
an allowed directory. Resolved files, including symlink targets, must remain
inside that root.

Image-upload failures return a bounded message and never echo the trusted
provider's local filesystem path to the user.

## Remote images

Remote fetching is disabled by default. Set `LARK_ALLOW_REMOTE_IMAGES=1` only
when required and list each trusted hostname in the comma-separated
`LARK_IMAGE_ALLOWED_HOSTS` setting. The channel then accepts only exact
allowlisted HTTPS hosts whose DNS results are globally routable.

Loopback, private, link-local, reserved, credential-bearing, and other
non-global targets remain blocked. Image count, encoded size, and NPY pixel
count are bounded by the corresponding environment settings.
