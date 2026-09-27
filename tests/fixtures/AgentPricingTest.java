// Hand-written deterministic fixture, NOT cloud-generated tests.
package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class AgentPricingTest {
    private final Pricing pricing = new Pricing();
    @Test void discount() { assertEquals(900, pricing.total(1000, false, "SAVE10")); }
    @Test void memberThreshold() { assertEquals(9500, pricing.total(10000, true, null)); }
    @Test void belowThreshold() { assertEquals(9999, pricing.total(9999, true, null)); }
    @Test void combined() { assertEquals(8500, pricing.total(10000, true, "SAVE10")); }
    @Test void nonMember() { assertEquals(10000, pricing.total(10000, false, null)); }
    @Test void zero() { assertEquals(0, pricing.total(0, true, "SAVE10")); }
    @Test void roundsDown() { assertEquals(90, pricing.total(101, false, "SAVE10")); }
    @Test void negative() { assertThrows(IllegalArgumentException.class, () -> pricing.total(-1, false, null)); }
    @Test void invalidCoupon() { assertThrows(IllegalArgumentException.class, () -> pricing.total(1000, false, "BOGUS")); }
}
