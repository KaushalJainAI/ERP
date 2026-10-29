from rest_framework.throttling import SimpleRateThrottle


class AssistantBurstThrottle(SimpleRateThrottle):
    scope = "assistant"

    def get_cache_key(self, request, view):
        ident = (
            request.user.pk
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AssistantDailyThrottle(SimpleRateThrottle):
    scope = "assistant_day"

    def get_cache_key(self, request, view):
        ident = (
            request.user.pk
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AssistantTranscribeThrottle(SimpleRateThrottle):
    scope = "assistant_stt"

    def get_cache_key(self, request, view):
        ident = (
            request.user.pk
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}
