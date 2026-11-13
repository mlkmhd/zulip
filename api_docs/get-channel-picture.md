{generate_api_header(/streams/{stream_id}/icon:get)}

## Parameters

{generate_api_arguments_table|zulip.yaml|/streams/{stream_id}/icon:get}

## Response

On success, the response body is a PNG image. If there is no picture or
the requested version is no longer current, the server returns HTTP 404.

## Example

```sh
curl --request GET https://zulip.example.com/api/v1/streams/7/icon \
    --user BOT_EMAIL:BOT_API_KEY --output channel-picture.png
```
