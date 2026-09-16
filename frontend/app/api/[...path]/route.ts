/** Same-origin proxy for the built frontend. The Python API remains loopback-only. */
export const dynamic = 'force-dynamic';
async function forward(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, 'http://127.0.0.1:8000');
  try {
    let body: Uint8Array | undefined;
    if (!['GET', 'HEAD'].includes(request.method) && request.body) {
      const maxBytes = 25 * 1024 * 1024;
      if (Number(request.headers.get('content-length')) > maxBytes)
        return Response.json(
          { detail: 'Request exceeds the 25 MB limit.' },
          { status: 413 },
        );
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) {
          await reader.cancel();
          return Response.json(
            { detail: 'Request exceeds the 25 MB limit.' },
            { status: 413 },
          );
        }
        chunks.push(value);
      }
      body = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        body.set(chunk, offset);
        offset += chunk.byteLength;
      }
    }
    const upstream = await fetch(target, {
      method: request.method,
      headers: Object.fromEntries(
        [
          'content-type',
          'cookie',
          'origin',
          'sec-fetch-site',
          'idempotency-key',
        ].flatMap((key) =>
          request.headers.has(key) ? [[key, request.headers.get(key)!]] : [],
        ),
      ),
      body: body as BodyInit | undefined,
      signal: request.signal,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        'Content-Type':
          upstream.headers.get('content-type') || 'application/json',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
        ...(upstream.headers.get('content-security-policy')
          ? {
              'Content-Security-Policy': upstream.headers.get(
                'content-security-policy',
              )!,
            }
          : {}),
        ...(upstream.headers.get('content-disposition')
          ? {
              'Content-Disposition': upstream.headers.get(
                'content-disposition',
              )!,
            }
          : {}),
        'X-Accel-Buffering': 'no',
        ...(upstream.headers.get('set-cookie')
          ? { 'Set-Cookie': upstream.headers.get('set-cookie')! }
          : {}),
      },
    });
  } catch {
    return Response.json(
      {
        detail:
          'The local execution API is unavailable. Start the backend and try again.',
      },
      { status: 502 },
    );
  }
}
export const GET = forward;
export const POST = forward;
export const PUT = forward;
