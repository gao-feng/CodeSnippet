#include <dlfcn.h>
#include <napi/native_api.h>
#include <string>

EXTERN_C_START
static napi_value CreateString(napi_env env, const std::string &value)
{
    napi_value result = nullptr;
    napi_create_string_utf8(env, value.c_str(), value.size(), &result);
    return result;
}

static napi_value DlopenTarget(napi_env env, napi_callback_info info)
{
    constexpr size_t argc = 1;
    napi_value args[argc] = {nullptr};
    size_t actualArgc = argc;
    napi_get_cb_info(env, info, &actualArgc, args, nullptr, nullptr);

    std::string path = "/data/storage/el1/bundle/libs/arm64/libmmkv.so";
//    if (actualArgc >= 1) {
//        size_t strSize = 0;
//        napi_get_value_string_utf8(env, args[0], nullptr, 0, &strSize);
//        char* buffer = new char[strSize + 1];
//        std::string input(strSize, '\0');
//        napi_get_value_string_utf8(env, args[0], buffer, strSize + 1, &strSize);
//        path = input;
//    }

    dlerror();
    void *handle = dlopen(path.c_str(), RTLD_NOW);
    if (handle == nullptr) {
        const char *error = dlerror();
        std::string message = "dlopen failed: ";
        message += (error == nullptr ? "unknown error" : error);
        return CreateString(env, message);
    }

    //dlclose(handle);
    return CreateString(env, "dlopen success");
}

static napi_value Init(napi_env env, napi_value exports)
{
    napi_property_descriptor desc[] = {
        {"dlopenTarget", nullptr, DlopenTarget, nullptr, nullptr, nullptr, napi_default, nullptr}
    };
    napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
    return exports;
}

EXTERN_C_END

static napi_module dlopenModule = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = Init,
    .nm_modname = "dlopen",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterDlopenModule(void)
{
    napi_module_register(&dlopenModule);
}
