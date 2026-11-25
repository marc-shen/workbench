#include <assert.h>
#import "../config/emacs/Emacs Client.app/Contents/Resources/client-menu.m"

@interface TestClientActions : ProfileClientActions
@property NSUInteger reopenCount;
@end
@implementation TestClientActions
- (void)handleReopen:(NSAppleEventDescriptor *)event withReplyEvent:(NSAppleEventDescriptor *)reply {
    self.reopenCount++;
}
@end

@interface TestExistingDelegate : NSObject <NSApplicationDelegate>
@end
@implementation TestExistingDelegate
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)visible {
    return NO;
}
@end

int main(void) {
    @autoreleasepool {
        NSMenu *menu = [NSMenu new];
        NSMenuItem *native = [menu addItemWithTitle:@"New Frame"
            action:NSSelectorFromString(@"newFrame:") keyEquivalent:@""];
        ProfileClientActions *actions = [ProfileClientActions new];
        configureClientMenu(menu, actions);
        configureClientMenu(menu, actions);
        assert(menu.numberOfItems == 1);
        assert([menu itemWithTag:19428] == native);
        assert(native.target == actions);
        assert(native.action == @selector(openClientFrame:));
        assert(native.enabled);
        NSMenu *empty = [NSMenu new];
        configureClientMenu(empty, actions);
        configureClientMenu(empty, actions);
        assert(empty.numberOfItems == 1);
        assert([empty itemWithTag:19428].action == @selector(openClientFrame:));
        // Exercise the AppleEvent entry point, not a direct delegate call.
        // Repeated installs must deliver one callback and preserve upstream
        // delegate behavior, even with no windows or native Emacs frames.
        TestExistingDelegate *delegate = [TestExistingDelegate new];
        TestClientActions *receiver = [TestClientActions new];
        NSAppleEventManager *events = [NSAppleEventManager sharedAppleEventManager];
        configureClientReopen(events, delegate, receiver);
        configureClientReopen(events, delegate, receiver);
        assert(![delegate applicationShouldHandleReopen:[NSApplication sharedApplication]
                                      hasVisibleWindows:NO]);
        NSAppleEventDescriptor *event = [NSAppleEventDescriptor
            appleEventWithEventClass:kCoreEventClass eventID:kAEReopenApplication
            targetDescriptor:nil returnID:kAutoGenerateReturnID transactionID:kAnyTransactionID];
        AppleEvent reply = {typeNull, NULL};
        OSStatus status = [events dispatchRawAppleEvent:event.aeDesc
                                          withRawReply:&reply handlerRefCon:(__bridge void *)receiver];
        assert(status == noErr);
        assert(receiver.reopenCount == 1);
        AEDisposeDesc(&reply);
        [events removeEventHandlerForEventClass:kCoreEventClass andEventID:kAEReopenApplication];
        puts("Dock New Frame and reopen AppleEvent routing: all checks passed");
    }
}
