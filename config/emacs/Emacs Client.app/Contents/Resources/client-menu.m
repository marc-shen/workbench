#import <Cocoa/Cocoa.h>
#import <objc/runtime.h>
#include <emacs-module.h>

int plugin_is_GPL_compatible;

@interface ProfileClientActions : NSObject
@end
static void activateClient(NSArray<NSString *> *arguments) {
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    task.arguments = [@[[[NSBundle mainBundle] pathForResource:@"activate-emacs" ofType:nil]]
                      arrayByAddingObjectsFromArray:arguments];
    task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    NSError *error = nil;
    // Never wait here: emacsclient needs this daemon's event loop to respond.
    if (![task launchAndReturnError:&error]) NSLog(@"Unable to open Emacs: %@", error);
}

@implementation ProfileClientActions
- (void)openClientFrame:(id)sender {
    activateClient(@[@"--new-window"]);
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)visible {
    activateClient(@[]);
    return YES;
}
- (void)handleReopen:(NSAppleEventDescriptor *)event withReplyEvent:(NSAppleEventDescriptor *)reply {
    activateClient(@[]);
}
- (void)restartEmacs:(id)sender {
    // Run confirmation/recovery outside the daemon that will be terminated.
    NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
    configuration.createsNewApplicationInstance = YES;
    configuration.arguments = @[@"--restart"];
    [[NSWorkspace sharedWorkspace] openApplicationAtURL:[NSBundle mainBundle].bundleURL
        configuration:configuration completionHandler:^(NSRunningApplication *app, NSError *error) {
            if (error) dispatch_async(dispatch_get_main_queue(), ^{
                NSAlert *alert = [NSAlert new];
                alert.messageText = @"无法打开 Emacs 恢复工具";
                alert.informativeText = error.localizedDescription;
                [alert runModal];
            });
        }];
}
@end

static ProfileClientActions *recovery;

static void configureClientReopen(NSAppleEventManager *events,
                                  id<NSApplicationDelegate> delegate,
                                  ProfileClientActions *actions) {
    SEL reopen = @selector(applicationShouldHandleReopen:hasVisibleWindows:);
    Method method = class_getInstanceMethod([ProfileClientActions class], reopen);
    // Retain the delegate fallback without replacing an upstream implementation.
    class_addMethod(object_getClass(delegate), reopen, method_getImplementation(method),
                    method_getTypeEncoding(method));
    // A launchd-started Emacs on macOS 27 can receive reopen AppleEvents without
    // AppKit calling the dynamically added delegate method. Register the actual
    // Dock/LaunchServices event as well, including after the last frame closes.
    // This replaces one handler rather than adding observers on every frame.
    [events setEventHandler:actions andSelector:@selector(handleReopen:withReplyEvent:)
             forEventClass:kCoreEventClass andEventID:kAEReopenApplication];
}

static void configureClientMenu(NSMenu *menu, ProfileClientActions *actions) {
    NSMenuItem *newFrame = [menu itemWithTag:19428];
    if (!newFrame) {
        for (NSMenuItem *item in menu.itemArray) {
            if (item.action == NSSelectorFromString(@"newFrame:")) {
                newFrame = item;
                break;
            }
        }
    }
    if (!newFrame) {
        newFrame = [menu insertItemWithTitle:@"New Frame"
            action:@selector(openClientFrame:) keyEquivalent:@"" atIndex:0];
    }
    // Native newFrame: posts an event against the selected GUI frame. With no
    // GUI frames there is no such target, and it does not activate a background
    // app. Route the Dock action through the same client + focus helper as reopen.
    newFrame.target = actions;
    newFrame.action = @selector(openClientFrame:);
    newFrame.tag = 19428;
    newFrame.enabled = YES;
}

static emacs_value installMenu(emacs_env *env, ptrdiff_t nargs, emacs_value *args, void *data) {
    @autoreleasepool {
        // Only the native process inside the unified Client bundle owns this menu.
        if (!NSApp || ![[NSBundle mainBundle].bundleIdentifier isEqualToString:@"local.marcshen.emacs-client"])
            return env->intern(env, "nil");
        id<NSApplicationDelegate> delegate = NSApp.delegate;
        if (![delegate respondsToSelector:@selector(applicationDockMenu:)])
            return env->intern(env, "nil");
        NSMenu *menu = [delegate applicationDockMenu:NSApp];
        if (!menu) return env->intern(env, "nil");
        if (!recovery) recovery = [ProfileClientActions new];
        configureClientMenu(menu, recovery);
        configureClientReopen([NSAppleEventManager sharedAppleEventManager], delegate, recovery);
        if (![menu itemWithTag:19427]) {
            [menu addItem:[NSMenuItem separatorItem]];
            NSMenuItem *item = [menu addItemWithTitle:@"强制重启 Emacs 后台…"
                action:@selector(restartEmacs:) keyEquivalent:@""];
            item.target = recovery;
            item.tag = 19427;
        }
        return env->intern(env, "t");
    }
}

int emacs_module_init(struct emacs_runtime *runtime) {
    if (runtime->size < (ptrdiff_t)sizeof(*runtime)) return 1;
    emacs_env *env = runtime->get_environment(runtime);
    if (env->size < (ptrdiff_t)sizeof(*env)) return 2;
    emacs_value binding[] = {
        env->intern(env, "profile-macos-client-menu-install"),
        env->make_function(env, 0, 0, installMenu,
            "Route New Frame through the client and add recovery to the unified Dock menu.", NULL)
    };
    env->funcall(env, env->intern(env, "fset"), 2, binding);
    return env->non_local_exit_check(env) == emacs_funcall_exit_return ? 0 : 3;
}
