"""Django admin for ``UserProfile`` (silent-auth profiles).

Lets an admin create per-user profiles and set their Alpaca sandbox keys. The
form accepts plaintext keys in the *new_api_key* / *new_secret_key* fields and
encrypts them on save. The raw encrypted values already in the DB are never
shown back as plaintext.
"""

from django import forms
from django.contrib import admin

from .models import UserProfile


class UserProfileForm(forms.ModelForm):
    """Form that encrypts freshly-entered Alpaca keys on save."""

    new_api_key = forms.CharField(
        required=False, label="Alpaca API key (plaintext)", widget=forms.PasswordInput
    )
    new_secret_key = forms.CharField(
        required=False,
        label="Alpaca secret key (plaintext)",
        widget=forms.PasswordInput,
    )

    class Meta:
        model = UserProfile
        fields = "__all__"

    def save(self, commit: bool = True):
        obj = super().save(commit=False)
        api = self.cleaned_data.get("new_api_key") or ""
        secret = self.cleaned_data.get("new_secret_key") or ""
        if api or secret:
            obj.store_credentials(api_key=api, secret_key=secret)
        if commit:
            obj.save()
        return obj


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    form = UserProfileForm
    list_display = ("slug", "display_name", "paper", "is_active", "credentials_ok")
    list_filter = ("paper", "is_active", "auto_approvisioned")
    search_fields = ("slug", "display_name")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "slug",
                    "display_name",
                    "is_active",
                    "auto_approvisioned",
                )
            },
        ),
        (
            "Alpaca sandbox credentials (encrypted at rest)",
            {"fields": ("new_api_key", "new_secret_key", "base_url", "data_url", "paper")},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Has credentials")
    def credentials_ok(self, obj: UserProfile):
        return obj.has_credentials
