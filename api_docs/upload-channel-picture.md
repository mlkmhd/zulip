{generate_api_header(/streams/{stream_id}/icon:post)}

## Parameters

{generate_api_arguments_table|zulip.yaml|/streams/{stream_id}/icon:post}

Send one image in the `file` field of a multipart form.

## Response

{generate_return_values_table|zulip.yaml|/streams/{stream_id}/icon:post}

```json
{"result": "success", "msg": "", "icon_url": "/json/streams/7/icon?v=345f1588-675a-406f-a86a-c8f77d606a7a"}
```

## Example

```sh
curl --request POST https://zulip.example.com/api/v1/streams/7/icon \
    --user BOT_EMAIL:BOT_API_KEY --form file=@channel-picture.png
```
