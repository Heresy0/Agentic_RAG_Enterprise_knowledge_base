<?php

// OpenMES integration extension, distributed under AGPL-3.0-or-later.
namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Laravel\Sanctum\PersonalAccessToken;

final class ReadOnlyIntegration
{
    public function handle(Request $request, Closure $next)
    {
        $token = auth('sanctum')->user()?->currentAccessToken();
        if ($token instanceof PersonalAccessToken && in_array('knowledge:read-only', $token->abilities ?? [], true)) {
            // The integration credential can only read these production resources.
            // Operator role alone is insufficient: upstream also lets operators start steps.
            if ($request->method() !== 'GET' || !preg_match(
                '~^api/v1/(work-orders(?:/[1-9][0-9]*(?:/batches)?)?|batches/[1-9][0-9]*)$~D',
                $request->path()
            )) {
                return response()->json(['message' => 'Read-only integration credential.'], 403);
            }
        }
        return $next($request);
    }
}
